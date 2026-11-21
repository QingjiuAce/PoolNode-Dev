"""Verified direct HTTPS downloads of caller-authorized Pool assets.

The caller checks the manifest and supplies a trusted output directory. No
manifest lookup, benchmark, network discovery, or configuration change here.
"""
from __future__ import annotations

import hashlib
import http.client
import os
from pathlib import Path
import re
import ssl
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from urllib.parse import urlsplit

_HOST = 'dl.bszx.site'
_CHUNK = 64 * 1024
_TIMEOUT = 15  # per blocking socket operation, not a whole-download deadline
_STRONG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]*"')


class _InvalidResponse(ValueError):
    """Invalid representation or no usable range support."""


class _NetworkError(OSError):
    """A transport failure; distinct from errors writing local storage."""


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


def _fetch(ctx, target, size, span=None, etag=None, sink=None):
    """One GET on a fresh connection. A None sink validates/discards the probe."""
    headers = {'Accept-Encoding': 'identity', 'Connection': 'close'}
    if span is not None:
        start, end = span
        headers['Range'] = f'bytes={start}-{end}'
        if etag is not None:
            headers['If-Range'] = etag
    count = size if span is None else end - start + 1
    conn = http.client.HTTPSConnection(_HOST, 443, timeout=_TIMEOUT, context=ctx)
    try:
        with _network():
            conn.request('GET', target, headers=headers)
            response = conn.getresponse()
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
            remaining = count
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
            return tag
    finally:
        conn.close()


def _get(*args, **kwargs):
    # Retry one transport failure from the start of the same assigned interval.
    try:
        return _fetch(*args, **kwargs)
    except _NetworkError:
        return _fetch(*args, **kwargs)


def _verify(path, size, expected):
    with path.open('rb') as f:
        if os.fstat(f.fileno()).st_size != size:
            raise _InvalidResponse('size mismatch')
        digest = hashlib.sha256()
        for block in iter(lambda: f.read(_CHUNK), b''):
            digest.update(block)
        if digest.hexdigest() != expected:
            raise _InvalidResponse('SHA-256 mismatch')


def _ranges(ctx, target, size, workers, stage):
    tag = _get(ctx, target, size, (0, 0))
    n = min(workers, size)

    def segment(i):
        span = (size * i // n, size * (i + 1) // n - 1)
        # Independent file descriptions: offsets cannot race across threads.
        with stage.open('r+b', buffering=0) as f:
            _get(ctx, target, size, span, tag, f)

    with ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(segment, range(n)))  # join all writers before fallback


def download(url: str, size: int, sha256: str, output: Path, workers: int) -> str:
    """Deliver a new file, returning 'range' or 'single' only after verification.

    workers is 1..16; one worker or fewer than two bytes uses a single GET.
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
                _ranges(ctx, target, size, workers, stage)
                _verify(stage, size, sha256.lower())
                mode = 'range'
            except (_InvalidResponse, _NetworkError):
                pass
        if mode == 'single':
            with stage.open('wb') as f:  # truncate any partially assembled object
                _get(ctx, target, size, sink=f)
            _verify(stage, size, sha256.lower())
        os.link(stage, output)  # atomic create-if-absent, no check/replace race
        return mode
    finally:
        stage.unlink()
