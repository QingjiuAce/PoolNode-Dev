"""Offline checks for scripts/peak_probe.py (no network)."""
import hashlib
import importlib.util
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'peak_probe.py'
spec = importlib.util.spec_from_file_location('peak_probe', SCRIPT)
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

DATA = b'probe fixture!' * 100
DIGEST = hashlib.sha256(DATA).hexdigest()
PATH = '/pool-tests/fixture.jpg'


class Reply(BytesIO):
    def __init__(self, data, status, fields):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for k, v in fields.items():
            self.headers[k] = v

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class Conn:
    calls = []
    corrupt = False

    def __init__(self, *args, **kwargs):
        self.headers = None

    def request(self, method, path, headers):
        assert method == 'GET' and path == PATH
        self.headers = headers
        type(self).calls.append(headers)

    def close(self):
        pass

    def getresponse(self):
        block = self.headers.get('Range')
        if block is None:
            return Reply(DATA, 200, {'Content-Length': str(len(DATA)), 'ETag': '"v1"'})
        start, end = map(int, block.split('=')[1].split('-'))
        body = DATA[start:end + 1]
        if type(self).corrupt and start > 0:
            body = bytes([body[0] ^ 0xFF]) + body[1:]
        return Reply(body, 206, {
            'Content-Range': f'bytes {start}-{end}/{len(DATA)}',
            'Content-Length': str(len(body)), 'ETag': '"v1"'})


@pytest.fixture
def mock(monkeypatch):
    Conn.calls = []
    Conn.corrupt = False
    monkeypatch.setattr(pp.http.client, 'HTTPSConnection', Conn)
    return Conn


def test_chunk_spans_exact():
    for size in (1, 2, 1400, 5_403_720):
        for n in (16, 32):
            spans = pp.chunk_spans(size, n)
            assert spans[0][0] == 0 and spans[-1][1] == size - 1
            for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
                assert e1 + 1 == s2  # contiguous => disjoint and ordered


def test_aggregate_units_regression():
    # 2026-11-22 修正口径：32 个完整文件的聚合，而非单流均摊
    assert pp.aggregate_mbps(32 * 5_403_720, 127.66) == pytest.approx(10.84, abs=0.02)
    assert pp.aggregate_mbps(5_403_720, 124.77) == pytest.approx(0.35, abs=0.01)


def test_ranges_arm_verifies_and_counts(mock, tmp_path):
    ctx = pp.ssl.create_default_context()
    arm = pp.run_ranges(ctx, PATH, len(DATA), DIGEST, 16, tmp_path, 5.0)
    assert arm['sha_ok'] and arm['status'] == 'ok'
    assert arm['seg_count'] == 16
    assert len(mock.calls) == 17  # probe + one request per segment
    assert arm['mbps'] and arm['mbps'] > 0
    assert arm['retries'] == 0
    assert arm['tail_ratio'] >= 1.0


def test_ranges_arm_detects_corruption(mock, tmp_path):
    mock.corrupt = True
    ctx = pp.ssl.create_default_context()
    arm = pp.run_ranges(ctx, PATH, len(DATA), DIGEST, 4, tmp_path, 5.0)
    assert not arm['sha_ok'] and arm['status'] == 'sha-mismatch'
    assert arm['mbps'] is None


def test_single_arm(mock, tmp_path):
    ctx = pp.ssl.create_default_context()
    arm = pp.run_single(ctx, PATH, len(DATA), DIGEST, tmp_path, 5.0)
    assert arm['sha_ok'] and len(mock.calls) == 1


def test_window_and_slot_guards():
    start, end = pp.parse_window('2026-11-23T02:00/2026-11-24T23:00')
    assert start < end
    assert {20, 21, 22, 23} <= set(pp.SLOT_HOURS)
    assert 1 not in pp.SLOT_HOURS and 19 not in pp.SLOT_HOURS


def test_parse_slots():
    assert pp.parse_slots('') == pp.SLOT_HOURS
    assert pp.parse_slots(' 12,16,20,21,22,23 ') == frozenset({12, 16, 20, 21, 22, 23})
    with pytest.raises(ValueError, match='invalid hour'):
        pp.parse_slots('24')
    with pytest.raises(ValueError, match='invalid hour'):
        pp.parse_slots('12,-1')
