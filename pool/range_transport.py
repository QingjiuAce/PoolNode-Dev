"""Verified direct HTTPS downloads of caller-authorized Pool assets.

The caller checks the manifest and supplies a trusted output directory. No
manifest lookup, benchmark, network discovery, or configuration change here.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
from pathlib import Path
import re
import secrets
import socket
import ssl
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

_HOST = 'dl.bszx.site'
_CHUNK = 64 * 1024
_TIMEOUT = 15  # per blocking socket operation, not a whole-download deadline
_STRONG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]*"')
_CHUNK_TARGET = 256 * 1024   # dynamic mode: target chunk size
_MAX_CHUNKS_PER_WORKER = 2   # dynamic mode: bounds the serial chunk chain per
                             # worker; measured TTFB on a reused conn is ~55 ms
                             # vs ~285 ms fresh, but each extra wave still pays it


class _InvalidResponse(ValueError):
    """Invalid representation or no usable range support."""


class _NetworkError(OSError):
    """A transport failure; distinct from errors writing local storage."""


class Cancelled(RuntimeError):
    """External cancellation requested: staging is discarded, no fallback."""


def _new_run_id():
    """One identifiable id per Metrics instance (experiment run)."""
    return time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4)


class Metrics:
    """Thread-safe collector of per-request diagnostics. Opt-in only: callers
    that never construct one execute the exact v0.4 code path. Records carry
    no headers, tokens, or addresses — only timings, sizes, spans, statuses.

    Each instance owns a run_id that is copied into every record, and dump()
    creates the target exclusively: an existing file is a hard error, never
    silently overwritten. Lines are written to a same-directory temporary file,
    fsynced, then hard-linked into place, so a failed or partial write never
    leaves a plausible-looking metrics file."""

    def __init__(self, run_id=None):
        self._lock = threading.Lock()
        self.records = []
        self._conn_seq = 0
        self.run_id = run_id or _new_run_id()

    def next_conn(self):
        with self._lock:
            self._conn_seq += 1
            return self._conn_seq

    def add(self, rec):
        with self._lock:
            record = dict(rec)
            record.setdefault('run_id', self.run_id)
            self.records.append(record)

    def dump(self, path):
        path = Path(path)
        tmp = path.with_name(
            f'.{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                fd = None
                for rec in self.records:
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise FileExistsError(
                    f'metrics 文件已存在，拒绝覆盖: {path}') from None
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        return path


class _TimedConnection(http.client.HTTPSConnection):
    """HTTPSConnection with DNS/TCP/TLS phase timing (metrics runs only).

    connect() stores the phase timings on .timing; the request that triggered
    the handshake consumes them. Mirrors the stdlib connect(): resolve all
    addresses, connect in order, then TLS-wrap with SNI and the caller's
    verification context.
    """

    def __init__(self, *args, counter, **kwargs):
        super().__init__(*args, **kwargs)
        self.id = counter.next_conn()
        self.timing = None

    def connect(self):
        t0 = time.perf_counter()
        addrinfo = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        t1 = time.perf_counter()
        err = None
        for family, stype, proto, _canon, sockaddr in addrinfo:
            try:
                sock = socket.socket(family, stype, proto)
            except OSError as exc:
                err = exc
                continue
            sock.settimeout(self.timeout)
            try:
                sock.connect(sockaddr)
            except OSError as exc:
                err = exc
                sock.close()
                continue
            err = None
            break
        if err is not None:
            raise err
        t2 = time.perf_counter()
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        t3 = time.perf_counter()
        self.timing = {'dns_ms': round((t1 - t0) * 1000, 2),
                       'tcp_ms': round((t2 - t1) * 1000, 2),
                       'tls_ms': round((t3 - t2) * 1000, 2)}


def _target(url):
    # urlsplit drops certain control characters and elides empty ?/# suffixes.
    if (not isinstance(url, str) or not url.isascii() or not url
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
            or any(c in url for c in ('?', '#', '\\'))):
        raise ValueError('invalid Pool HTTPS URL (percent-encode path characters)')
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc.lower() not in (_HOST, _HOST + ':443'):
        raise ValueError('expected the Pool HTTPS origin without credentials')
    return parts.path or '/'


def _header(response, name):
    values = response.headers.get_all(name, [])
    if len(values) > 1:
        raise _InvalidResponse(f'ambiguous {name}')
    return values[0] if values else None


@contextmanager
def _network():
    try:
        yield
    except ssl.SSLCertVerificationError:
        raise  # never retry certificate failures or downgrade verification
    except (OSError, http.client.HTTPException) as exc:
        raise _NetworkError('Pool HTTPS transport failed') from exc


def _fetch(ctx, target, size, span=None, etag=None, sink=None, metrics=None,
           label='', attempt=1, conn=None):
    """One GET. A None sink validates/discards the probe.

    conn=None creates a fresh connection closed on exit (v0.4 behaviour). A
    caller-provided conn is reused as-is and left open; if the server or
    network has closed it, the next request fails into the caller's retry
    path. A metrics collector only adds phase-timing records — request and
    validation behaviour is unchanged either way.
    """
    headers = {'Accept-Encoding': 'identity'}
    if conn is None:
        headers['Connection'] = 'close'
    if span is not None:
        start, end = span
        headers['Range'] = f'bytes={start}-{end}'
        if etag is not None:
            headers['If-Range'] = etag
    count = size if span is None else end - start + 1
    remaining = count
    rec = None
    own = conn is None
    if metrics is not None:
        rec = {'kind': 'request', 'ts': round(time.time(), 3), 'label': label,
               'span': span, 'expected_bytes': count, 'attempt': attempt}
        if own:
            conn = _TimedConnection(_HOST, 443, timeout=_TIMEOUT, context=ctx,
                                    counter=metrics)
            rec['conn_id'] = conn.id
        else:
            rec['conn_id'] = getattr(conn, 'id', None)
    elif own:
        conn = http.client.HTTPSConnection(_HOST, 443, timeout=_TIMEOUT, context=ctx)
    t_req = time.perf_counter() if rec is not None else None
    t_body = None
    try:
        with _network():
            conn.request('GET', target, headers=headers)
            response = conn.getresponse()
        if rec is not None:
            rec['status'] = response.status
            rec['ttfb_ms'] = round((time.perf_counter() - t_req) * 1000, 2)
            if isinstance(conn, _TimedConnection) and conn.timing is not None:
                rec.update(conn.timing)  # the handshake happened in this request
                rec['new_conn'] = True
                conn.timing = None
            else:
                rec['new_conn'] = False
            t_body = time.perf_counter()
        with response:
            if 300 <= response.status < 400:
                raise ValueError('redirects and unsolicited 3xx responses are forbidden')
            if response.status in (401, 403, 404, 410, 429, 503):
                raise ValueError(f'Pool refused or unavailable: HTTP {response.status}')
            if response.status != (200 if span is None else 206):
                raise _InvalidResponse(f'unexpected HTTP status {response.status}')
            if (_header(response, 'Content-Encoding') or 'identity').lower() != 'identity':
                raise _InvalidResponse('encoded response')
            length = _header(response, 'Content-Length')
            transfer = _header(response, 'Transfer-Encoding')
            if transfer is not None and (transfer.lower() != 'chunked' or length is not None):
                raise _InvalidResponse('unsupported or conflicting HTTP framing')
            if length is not None and length != str(count):
                raise _InvalidResponse('incorrect Content-Length')
            cr = _header(response, 'Content-Range')
            tag = None
            if span is not None:
                if cr != f'bytes {start}-{end}/{size}':
                    raise _InvalidResponse('incorrect Content-Range')
                tag = _header(response, 'ETag')
                if not tag or not _STRONG.fullmatch(tag) or (etag is not None and etag != tag):
                    raise _InvalidResponse('missing, weak, or changed ETag')
            elif cr is not None:
                raise _InvalidResponse('partial representation in single GET')
            if sink is not None:
                sink.seek(0 if span is None else start)
            while remaining:
                with _network():
                    block = response.read(min(_CHUNK, remaining))
                if not block:
                    raise _InvalidResponse('short HTTP body')
                if sink is not None and sink.write(block) != len(block):
                    raise OSError('short local write')
                remaining -= len(block)
            with _network():
                if response.read(1):
                    raise _InvalidResponse('oversized HTTP body')
        if rec is not None:
            rec['body_ms'] = round((time.perf_counter() - t_body) * 1000, 2)
            rec['bytes'] = count - remaining
            metrics.add(rec)
        return tag
    except BaseException as exc:
        if rec is not None:
            rec['error'] = exc.__class__.__name__
            if t_body is not None:
                rec['body_ms'] = round((time.perf_counter() - t_body) * 1000, 2)
                rec['bytes'] = count - remaining
            metrics.add(rec)
        raise
    finally:
        if own:
            conn.close()


def _get(*args, **kwargs):
    # Retry one transport failure from the start of the same assigned interval.
    # Never call with a caller-owned conn: the retry must start on a fresh one.
    try:
        return _fetch(*args, attempt=1, **kwargs)
    except _NetworkError:
        return _fetch(*args, attempt=2, **kwargs)


def _verify(path, size, expected):
    with path.open('rb') as f:
        if os.fstat(f.fileno()).st_size != size:
            raise _InvalidResponse('size mismatch')
        digest = hashlib.sha256()
        for block in iter(lambda: f.read(_CHUNK), b''):
            digest.update(block)
        if digest.hexdigest() != expected:
            raise _InvalidResponse('SHA-256 mismatch')


def _ranges(ctx, target, size, workers, stage, metrics=None):
    tag = _get(ctx, target, size, (0, 0), metrics=metrics, label='probe')
    n = min(workers, size)

    def segment(i):
        span = (size * i // n, size * (i + 1) // n - 1)
        # Independent file descriptions: offsets cannot race across threads.
        with stage.open('r+b', buffering=0) as f:
            _get(ctx, target, size, span, tag, f, metrics=metrics, label=f'seg{i}')

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(segment, range(n)))  # join all writers before fallback


def _chunk_spans(size, workers):
    """Dynamic plan: chunks aim at _CHUNK_TARGET; small files get fewer
    effective workers instead of tiny chunks; request count is bounded by
    _MAX_CHUNKS_PER_WORKER * workers. Boundaries use the same integer
    division as the static path, so the union is exact and disjoint."""
    n = max(1, -(-size // _CHUNK_TARGET))
    n = min(n, workers * _MAX_CHUNKS_PER_WORKER, size)
    return [(size * i // n, size * (i + 1) // n - 1) for i in range(n)]


def _open_conn(ctx, metrics=None):
    if metrics is not None:
        return _TimedConnection(_HOST, 443, timeout=_TIMEOUT, context=ctx,
                                counter=metrics)
    return http.client.HTTPSConnection(_HOST, 443, timeout=_TIMEOUT, context=ctx)


def _dynamic(ctx, target, size, workers, stage, metrics=None, cancel=None):
    """Chunk-queue scheduler (v0.5): workers claim chunks from a shared queue
    and own one keep-alive connection reused across their chunks.

    Same protocol and validation primitives as the static path (probe, per
    request Content-Range/ETag/If-Range checks, one _NetworkError retry per
    chunk on a fresh connection); any failure beyond that aborts into the
    download-level single fallback. An externally set cancel Event aborts the
    arm with Cancelled and no fallback (best-effort: in-flight chunks finish).
    A single-chunk plan must not come here.
    """
    spans = _chunk_spans(size, workers)
    if len(spans) < 2:
        raise _InvalidResponse('single-chunk plan belongs to the single GET path')
    tag = _get(ctx, target, size, (0, 0), metrics=metrics, label='probe')
    pending = queue.Queue()
    chunks = [{'id': i, 'span': span, 'state': 'QUEUED', 'worker': None,
               'retries': 0, 't_claim': None, 't_done': None}
              for i, span in enumerate(spans)]
    for chunk in chunks:
        pending.put(chunk)
    stop = threading.Event()
    lock = threading.Lock()

    def worker(wid):
        conn = None
        sink = stage.open('r+b', buffering=0)
        try:
            while not stop.is_set():
                if cancel is not None and cancel.is_set():
                    stop.set()
                    raise Cancelled('download cancelled')
                try:
                    chunk = pending.get_nowait()
                except queue.Empty:
                    return
                if stop.is_set():
                    with lock:
                        chunk['state'] = 'CANCELLED'
                    continue
                chunk['worker'] = wid
                chunk['t_claim'] = time.perf_counter()
                with lock:
                    chunk['state'] = 'IN_PROGRESS'
                try:
                    attempt = 1
                    while True:
                        if conn is None:
                            try:
                                conn = _open_conn(ctx, metrics)
                            except (OSError, http.client.HTTPException) as exc:
                                # Connection setup is transport: retry once, then
                                # fall back rather than fail the whole download.
                                raise _NetworkError('connection setup failed') from exc
                        try:
                            _fetch(ctx, target, size, chunk['span'], tag, sink,
                                   metrics=metrics,
                                   label=f'w{wid}c{chunk["id"]}', attempt=attempt,
                                   conn=conn)
                            break
                        except _NetworkError:
                            conn.close()
                            conn = None  # retry once from a fresh connection
                            if attempt >= 2:
                                raise
                            with lock:
                                chunk['retries'] += 1  # count scheduled retries only
                            attempt = 2
                except Cancelled:
                    with lock:
                        chunk['state'] = 'CANCELLED'
                    raise
                except BaseException:
                    stop.set()
                    with lock:
                        chunk['state'] = 'FAILED'
                    raise
                chunk['t_done'] = time.perf_counter()
                with lock:
                    chunk['state'] = 'SUCCEEDED'
                if conn.sock is None:  # server closed it after this response
                    conn = None
        finally:
            sink.close()
            if conn is not None:
                conn.close()

    n_workers = min(workers, len(spans))
    try:
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            list(pool.map(worker, range(n_workers)))
    finally:
        stop.set()  # success: no-op; failure/cancel: stop draining work we discard
        if metrics is not None:
            metrics.add({'kind': 'chunks', 'ts': round(time.time(), 3),
                         'scheduler': 'dynamic', 'workers': n_workers,
                         'chunks': chunks})


def download(url: str, size: int, sha256: str, output: Path, workers: int,
             metrics=None, scheduler: str = 'static', cancel=None) -> str:
    """Deliver a new file, returning 'range' or 'single' only after verification.

    workers is 1..16; one worker or fewer than two bytes uses a single GET.
    scheduler selects 'static' (v0.4 fixed equal-split, the default and
    fallback) or 'dynamic' (v0.5 chunk queue with connection reuse); both
    share the same probe, validation, fallback, and publication semantics.
    cancel (optional threading.Event) aborts the dynamic arm with Cancelled
    and no fallback; the static arm does not observe it.
    metrics (optional) records per-request phase timings without changing
    transport or validation behaviour in any way.
    Range protocol/transport/integrity failures discard all staged bytes and
    fall back once. TLS verification failures, redirects and local I/O failures
    are terminal. Publication requires same-directory hard-link support and
    never replaces an existing name, including a dangling symlink.
    """
    target = _target(url)
    if type(size) is not int or size < 0:
        raise ValueError('invalid manifest size')
    if not isinstance(sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', sha256):
        raise ValueError('invalid manifest SHA-256')
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError('workers must be an integer from 1 to 16')
    if scheduler not in ('static', 'dynamic'):
        raise ValueError("scheduler must be 'static' or 'dynamic'")
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError(output)
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(['http/1.1'])
    mode = 'single'
    fd, name = tempfile.mkstemp(dir=output.parent, prefix='.pool-', suffix='.part')
    stage = Path(name)
    try:
        os.close(fd)
        if workers > 1 and size >= 2:
            try:
                if scheduler == 'dynamic':
                    _dynamic(ctx, target, size, workers, stage, metrics, cancel)
                else:
                    _ranges(ctx, target, size, workers, stage, metrics)
                _verify(stage, size, sha256.lower())
                mode = 'range'
            except (_InvalidResponse, _NetworkError):
                pass
        if mode == 'single':
            with stage.open('wb') as f:  # truncate any partially assembled object
                _get(ctx, target, size, sink=f, metrics=metrics, label='single')
            _verify(stage, size, sha256.lower())
        os.link(stage, output)  # atomic create-if-absent, no check/replace race
        return mode
    finally:
        stage.unlink()
