"""Offline regression checks; no network or throughput measurement."""
from email.message import Message
from hashlib import sha256
from io import BytesIO
import pathlib
from pathlib import Path
import threading
import time

import pytest

from pool import fetch, range_transport as rt

DATA = b'pool fixture!' * 37
DIGEST = sha256(DATA).hexdigest()
URL = 'https://dl.bszx.site/fixture.jpg'


class Reply(BytesIO):
    def __init__(self, data, status, fields):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for k, v in fields.items():
            self.headers[k] = v


@pytest.mark.parametrize('fault,expected', [(None, 'range'), ('range', 'single'),
                                           ('tag', 'single'), ('bad_all', None)])
def test_verified_publish(monkeypatch, tmp_path, fault, expected):
    calls = []

    class Conn:
        def __init__(self, *args, **kwargs):
            self.headers = None

        def request(self, method, path, headers):
            assert method == 'GET' and path == '/fixture.jpg'
            assert self.headers is None  # each GET needs its own connection
            self.headers = headers
            calls.append(headers)

        def close(self):
            pass

        def getresponse(self):
            block = self.headers.get('Range')
            source = DATA if fault != 'bad_all' else DATA[::-1]
            if block is None:
                return Reply(source, 200, {})
            start, end = map(int, block.split('=')[1].split('-'))
            span = f'bytes {start}-{end}/{len(DATA)}'
            conditional = 'If-Range' in self.headers
            if conditional:
                assert self.headers['If-Range'] == '"v1"'
            return Reply(source[start:end+1], 206, {
                'Content-Range': 'wrong' if conditional and fault == 'range' else span,
                'ETag': '"v2"' if conditional and fault == 'tag' else '"v1"'})

    monkeypatch.setattr(rt.http.client, 'HTTPSConnection', Conn)
    out = tmp_path / 'out.jpg'
    if expected is None:
        with pytest.raises(ValueError, match='SHA-256'):
            rt.download(URL, len(DATA), DIGEST, out, 4)
        assert not out.exists()
    else:
        assert rt.download(URL, len(DATA), DIGEST, out, 4) == expected
        assert out.read_bytes() == DATA
        with pytest.raises(FileExistsError):
            rt.download(URL, len(DATA), DIGEST, out, 4)
    assert not list(tmp_path.glob('*.part'))
    assert sum('Range' not in headers for headers in calls) == (0 if expected == 'range' else 1)
    if expected == 'range':
        assert len(calls) == 5


@pytest.mark.parametrize('asset', ['example-real-pending', 'example-denied', 'test-poc-001'])
def test_unapproved_or_unpinned_never_calls_transport(monkeypatch, tmp_path, asset):
    monkeypatch.setattr(fetch, 'download', lambda *a: pytest.fail('unexpected download'))
    assets = Path(__file__).resolve().parents[1] / 'config/assets.example.yaml'
    assert fetch.main([asset, str(tmp_path/'out'), '--assets', str(assets)]) == 1


# --- v0.5 dynamic scheduler -------------------------------------------------

def test_chunk_spans_cover_exactly():
    """Dynamic plan: exact, contiguous, disjoint coverage of [0, size)."""
    for size in (1, 2, 3, 65_536, 256 * 1024, 256 * 1024 + 1, 1_778_514,
                 5_403_720, 17_000_000, 20 * 1024 * 1024):
        for w in (1, 4, 16):
            spans = rt._chunk_spans(size, w)
            assert spans[0][0] == 0 and spans[-1][1] == size - 1
            for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
                assert e1 + 1 == s2  # contiguous, therefore disjoint and ordered
            assert all(e >= s for s, e in spans)
            assert len(spans) <= w * rt._MAX_CHUNKS_PER_WORKER
    assert len(rt._chunk_spans(100 * 1024, 16)) == 1      # small file: 1 chunk
    assert len(rt._chunk_spans(1_778_514, 16)) == 7       # workers shrink, not chunks
    assert len(rt._chunk_spans(5_403_720, 16)) == 21      # > workers: queue feeds reuse


def test_download_argument_validation(tmp_path):
    with pytest.raises(ValueError, match='workers'):
        rt.download(URL, len(DATA), DIGEST, tmp_path / 'a', 17)
    with pytest.raises(ValueError, match='scheduler'):
        rt.download(URL, len(DATA), DIGEST, tmp_path / 'b', 4, scheduler='rr')


class ChunkyConn:
    """Mock origin for dynamic-scheduler tests: configurable per-request
    faults, keep-alive connection reuse, per-connection bookkeeping."""

    fail = None          # callable(headers_dict) -> Exception or None
    serve = None         # callable(start, end) -> Reply, overrides the body
    calls = None         # list of header dicts, reset per test
    instances = None     # list of created mock connections, reset per test

    def __init__(self, *args, **kwargs):
        self.headers = None
        self.sock = object()  # pretend the connection is alive for reuse
        type(self).instances.append(self)

    def request(self, method, path, headers):
        assert method == 'GET' and path == '/fixture.jpg'
        self.headers = headers
        type(self).calls.append(headers)

    def close(self):
        self.sock = None

    def getresponse(self):
        headers = self.headers
        probe = 'If-Range' not in headers  # the probe sends no If-Range
        if type(self).fail is not None and not probe:
            exc = type(self).fail(headers)
            if exc is not None:
                raise exc
        block = headers.get('Range')
        if block is None:
            return Reply(DATA, 200, {'ETag': '"v1"'})
        start, end = map(int, block.split('=')[1].split('-'))
        if not probe and type(self).serve is not None:
            return type(self).serve(start, end)
        if 'If-Range' in headers:
            assert headers['If-Range'] == '"v1"'
        return Reply(DATA[start:end+1], 206, {
            'Content-Range': f'bytes {start}-{end}/{len(DATA)}',
            'ETag': '"v1"'})


class TimedMockConn(ChunkyConn):
    """Stands in for _TimedConnection so metrics runs never touch the network."""

    def __init__(self, *args, counter, **kwargs):
        super().__init__(*args, **kwargs)
        self.id = counter.next_conn()
        self.timing = {'dns_ms': 0.0, 'tcp_ms': 0.0, 'tls_ms': 0.0}


def dynamic_setup(monkeypatch, chunk_target=32):
    monkeypatch.setattr(rt, '_CHUNK_TARGET', chunk_target)
    monkeypatch.setattr(rt.http.client, 'HTTPSConnection', ChunkyConn)
    monkeypatch.setattr(rt, '_TimedConnection', TimedMockConn)
    ChunkyConn.calls = []
    ChunkyConn.instances = []
    ChunkyConn.fail = None
    ChunkyConn.serve = None
    return rt._chunk_spans(len(DATA), 8)


def span_header(span):
    return f'bytes={span[0]}-{span[1]}'


def test_dynamic_publish_reuses_connections(monkeypatch, tmp_path):
    spans = dynamic_setup(monkeypatch)
    out = tmp_path / 'out.jpg'
    metrics = rt.Metrics()
    assert rt.download(URL, len(DATA), DIGEST, out, 8, metrics, 'dynamic') == 'range'
    assert out.read_bytes() == DATA
    n = len(spans)
    assert len(ChunkyConn.calls) == n + 1          # probe + every chunk exactly once
    requested = {h['Range'] for h in ChunkyConn.calls if 'If-Range' in h}
    assert requested == {span_header(s) for s in spans}  # no duplicates, no gaps
    chunk_rec = [r for r in metrics.records if r['kind'] == 'chunks'][0]
    assert all(c['state'] == 'SUCCEEDED' for c in chunk_rec['chunks'])
    assert sum(c['retries'] for c in chunk_rec['chunks']) == 0
    # connection accounting: probe + one keep-alive conn per worker that
    # actually claimed chunks; chunks outnumber workers, so keep-alive reuse
    # must have happened (more requests than connections).
    active = {c['worker'] for c in chunk_rec['chunks']}
    assert len(ChunkyConn.instances) == 1 + len(active)
    assert len(ChunkyConn.calls) > len(ChunkyConn.instances)


def test_dynamic_single_chunk_plan_uses_single_get(monkeypatch, tmp_path):
    dynamic_setup(monkeypatch, chunk_target=1 << 20)  # 481 B file -> 1 chunk plan
    out = tmp_path / 'out.jpg'
    assert rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic') == 'single'
    assert out.read_bytes() == DATA
    assert len(ChunkyConn.calls) == 1              # no probe, one plain GET


def test_dynamic_chunk_short_body_falls_back(monkeypatch, tmp_path):
    dynamic_setup(monkeypatch)

    def serve(start, end):
        return Reply(DATA[start:end], 206, {  # one byte short
            'Content-Range': f'bytes {start}-{end}/{len(DATA)}', 'ETag': '"v1"'})
    ChunkyConn.serve = serve
    out = tmp_path / 'out.jpg'
    assert rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic') == 'single'
    assert out.read_bytes() == DATA
    assert not list(tmp_path.glob('*.part'))


def test_dynamic_etag_change_falls_back(monkeypatch, tmp_path):
    dynamic_setup(monkeypatch)
    ChunkyConn.serve = lambda start, end: Reply(DATA[start:end+1], 206, {
        'Content-Range': f'bytes {start}-{end}/{len(DATA)}', 'ETag': '"v2"'})
    out = tmp_path / 'out.jpg'
    assert rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic') == 'single'
    assert out.read_bytes() == DATA


def test_dynamic_server_ignores_range_falls_back(monkeypatch, tmp_path):
    dynamic_setup(monkeypatch)
    ChunkyConn.serve = lambda start, end: Reply(DATA, 200, {'ETag': '"v1"'})
    out = tmp_path / 'out.jpg'
    assert rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic') == 'single'
    assert out.read_bytes() == DATA


def test_dynamic_one_network_failure_retries_chunk(monkeypatch, tmp_path):
    """One transport failure: the same chunk retries once on a fresh connection."""
    spans = dynamic_setup(monkeypatch)
    victim = span_header(spans[0])
    state = {'failed': False}

    def fail(headers):
        if headers.get('Range') == victim and not state['failed']:
            state['failed'] = True
            return TimeoutError('simulated stall')
        return None
    ChunkyConn.fail = fail
    out = tmp_path / 'out.jpg'
    metrics = rt.Metrics()
    assert rt.download(URL, len(DATA), DIGEST, out, 8, metrics, 'dynamic') == 'range'
    assert out.read_bytes() == DATA
    chunk_rec = [r for r in metrics.records if r['kind'] == 'chunks'][0]
    assert sum(c['retries'] for c in chunk_rec['chunks']) == 1
    assert all(c['state'] == 'SUCCEEDED' for c in chunk_rec['chunks'])


def test_dynamic_persistent_failure_falls_back_and_cancels(monkeypatch, tmp_path):
    """Both attempts of one chunk fail: fallback to single; all workers exit;
    nothing is published from staged bytes."""
    spans = dynamic_setup(monkeypatch)
    victim = span_header(spans[1])

    def fail(headers):
        if headers.get('Range') == victim:
            return ConnectionResetError('worker disconnected')
        return None
    ChunkyConn.fail = fail
    out = tmp_path / 'out.jpg'
    metrics = rt.Metrics()
    assert rt.download(URL, len(DATA), DIGEST, out, 8, metrics, 'dynamic') == 'single'
    assert out.read_bytes() == DATA
    assert not list(tmp_path.glob('*.part'))
    chunk_rec = [r for r in metrics.records if r['kind'] == 'chunks'][0]
    states = [c['state'] for c in chunk_rec['chunks']]
    assert 'FAILED' in states and 'IN_PROGRESS' not in states


class ClosingConn(ChunkyConn):
    def getresponse(self):
        reply = super().getresponse()
        self.sock = None  # server terminated the keep-alive after this response
        return reply


def test_dynamic_server_closes_keepalive_reopens(monkeypatch, tmp_path):
    """If the server closes after every response, workers safely reopen."""
    spans = dynamic_setup(monkeypatch)
    monkeypatch.setattr(rt.http.client, 'HTTPSConnection', ClosingConn)
    ChunkyConn.calls = []
    ChunkyConn.instances = []
    out = tmp_path / 'out.jpg'
    assert rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic') == 'range'
    assert out.read_bytes() == DATA
    n = len(spans)
    assert len(ChunkyConn.calls) == n + 1
    assert len(ChunkyConn.instances) == n + 1      # degraded to one conn per request


def test_dynamic_corrupt_chunk_recovers_via_single(monkeypatch, tmp_path):
    """A chunk with valid framing but wrong bytes fails the staged SHA check;
    the single-GET fallback re-downloads clean bytes and only the verified
    file is published — corrupt staged data never reaches the output."""
    dynamic_setup(monkeypatch)

    def serve(start, end):
        body = bytearray(DATA[start:end+1])
        body[0] ^= 0xFF
        return Reply(bytes(body), 206, {
            'Content-Range': f'bytes {start}-{end}/{len(DATA)}', 'ETag': '"v1"'})
    ChunkyConn.serve = serve
    out = tmp_path / 'out.jpg'
    assert rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic') == 'single'
    assert out.read_bytes() == DATA  # verified content, not the corrupted staging
    assert not list(tmp_path.glob('*.part'))


def test_dynamic_publish_failure_is_terminal(monkeypatch, tmp_path):
    """os.link failure (publication) is a local I/O error, not a fallback."""
    dynamic_setup(monkeypatch)
    out = tmp_path / 'out.jpg'

    def broken_link(src, dst):
        raise OSError('publication failed')
    monkeypatch.setattr(rt.os, 'link', broken_link)
    with pytest.raises(OSError, match='publication failed'):
        rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic')
    assert not out.exists()
    assert not list(tmp_path.glob('*.part'))


# --- v0.5 merge-gate tests T1-T5 (docs/V05_AUDIT.md) -----------------------

def test_t1_local_sink_open_failure_is_terminal(monkeypatch, tmp_path):
    """T1: worker cannot open its staging handle -> terminal, no fallback."""
    dynamic_setup(monkeypatch)
    real_open = pathlib.Path.open

    def fake_open(self, mode='r', *args, **kwargs):
        if mode == 'r+b':
            raise OSError('simulated sink open failure')
        return real_open(self, mode, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, 'open', fake_open)
    out = tmp_path / 'out.jpg'
    with pytest.raises(OSError, match='sink open'):
        rt.download(URL, len(DATA), DIGEST, out, 8, None, 'dynamic')
    assert not out.exists()
    assert not list(tmp_path.glob('*.part'))


def test_t2_short_local_write_is_terminal(monkeypatch):
    """T2: disk-full style short write -> OSError('short local write')."""
    dynamic_setup(monkeypatch)

    class ShortSink:
        def seek(self, *args, **kwargs):
            pass

        def write(self, block):
            return 0

    ctx = rt.ssl.create_default_context()
    with pytest.raises(OSError, match='short local write'):
        rt._fetch(ctx, '/fixture.jpg', len(DATA), None, None, ShortSink())


def test_t3_external_cancel_aborts_without_publish(monkeypatch, tmp_path):
    """T3: external cancel set mid-download -> Cancelled, no publish, no
    IN_PROGRESS leftovers (in-flight chunks may finish, queue stays queued)."""
    dynamic_setup(monkeypatch)
    cancel = threading.Event()
    seen = {'chunks': 0}
    real_fetch = rt._fetch

    def counting_fetch(*args, **kwargs):
        if kwargs.get('label', '').startswith('w'):
            seen['chunks'] += 1
            if seen['chunks'] >= 2:
                cancel.set()
        return real_fetch(*args, **kwargs)

    monkeypatch.setattr(rt, '_fetch', counting_fetch)
    out = tmp_path / 'out.jpg'
    metrics = rt.Metrics()
    with pytest.raises(rt.Cancelled):
        rt.download(URL, len(DATA), DIGEST, out, 8, metrics, 'dynamic', cancel)
    assert not out.exists()
    assert not list(tmp_path.glob('*.part'))
    chunk_rec = [r for r in metrics.records if r['kind'] == 'chunks'][0]
    states = [c['state'] for c in chunk_rec['chunks']]
    assert 'IN_PROGRESS' not in states
    assert 'SUCCEEDED' in states and 'QUEUED' in states


def test_t4_retry_exhausted_chunk_fails_then_single_fallback(monkeypatch, tmp_path):
    """T4: one chunk fails twice -> FAILED + others SUCCEEDED; the parallel
    assembly is discarded and only the verified single-GET fallback publishes."""
    spans = dynamic_setup(monkeypatch)
    victim = span_header(spans[0])
    def slow_reset(headers):
        if headers.get('Range') == victim:
            time.sleep(0.02)  # let other workers complete a chunk first
            return ConnectionResetError('reset')
        return None
    ChunkyConn.fail = slow_reset
    out = tmp_path / 'out.jpg'
    metrics = rt.Metrics()
    assert rt.download(URL, len(DATA), DIGEST, out, 8, metrics, 'dynamic') == 'single'
    assert out.read_bytes() == DATA
    chunk_rec = [r for r in metrics.records if r['kind'] == 'chunks'][0]
    states = [c['state'] for c in chunk_rec['chunks']]
    assert states.count('FAILED') == 1
    assert 'SUCCEEDED' in states
    assert 'IN_PROGRESS' not in states
    failed = [c for c in chunk_rec['chunks'] if c['state'] == 'FAILED'][0]
    assert failed['retries'] == 1


def test_t5_connection_setup_failure_falls_back(monkeypatch, tmp_path):
    """T5: scheduler cannot create a connection -> treated as transport,
    one retry, then single fallback; no temporary residue."""
    dynamic_setup(monkeypatch)

    def broken_open(*args, **kwargs):
        raise OSError('connection setup failed')

    monkeypatch.setattr(rt, '_open_conn', broken_open)
    out = tmp_path / 'out.jpg'
    metrics = rt.Metrics()
    assert rt.download(URL, len(DATA), DIGEST, out, 8, metrics, 'dynamic') == 'single'
    assert out.read_bytes() == DATA
    chunk_rec = [r for r in metrics.records if r['kind'] == 'chunks'][0]
    assert 'FAILED' in [c['state'] for c in chunk_rec['chunks']]
    assert not list(tmp_path.glob('*.part'))


@pytest.mark.parametrize('size,expected', [(1_700_000, 'static'), (5_000_000, 'dynamic')])
def test_auto_scheduler_resolution(monkeypatch, tmp_path, size, expected):
    """--scheduler auto (default): >=4MiB -> dynamic, small files -> static.
    The 4MiB crossover is the key user-visible default of v0.5."""
    captured = {}
    monkeypatch.setattr(fetch, 'select',
                        lambda *a: ('https://dl.bszx.site/x.jpg', 'a' * 64, size))

    def fake_download(url, sz, sha, output, workers, metrics, scheduler):
        captured.update(scheduler=scheduler, size=sz)
        return 'range'
    monkeypatch.setattr(fetch, 'download', fake_download)
    rc = fetch.main(['x', str(tmp_path / 'out.jpg'), '--assets', 'any.yaml'])
    assert rc == 0
    assert captured == {'scheduler': expected, 'size': size}
