"""Offline regression checks; no network or throughput measurement."""
from email.message import Message
from hashlib import sha256
from io import BytesIO
from pathlib import Path
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
