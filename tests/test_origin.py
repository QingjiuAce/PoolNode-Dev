"""Origin configuration: defaults stay production, custom origins stay strict."""
from __future__ import annotations

import pathlib
import ssl

import pytest

from pool import fetch, range_transport as rt
from pool.origin import DEFAULT_ORIGIN, normalize_origin, resolve_origin

REPO = pathlib.Path(__file__).resolve().parents[1]
BENCH_ASSETS = REPO / 'config' / 'bench-assets.yaml'


def test_default_origin_is_production():
    assert normalize_origin(DEFAULT_ORIGIN) == 'https://dl.bszx.site'
    assert resolve_origin(env={}) == DEFAULT_ORIGIN


def test_custom_https_origin_normalizes_host_and_port():
    assert normalize_origin('https://Mirror.Example:8443') == 'https://mirror.example:8443'
    assert normalize_origin('https://mirror.example./') == 'https://mirror.example'


def test_resolution_precedence_cli_env_default():
    env = {'POOL_ORIGIN': 'https://env.example'}
    assert resolve_origin('https://cli.example', env=env) == 'https://cli.example'
    assert resolve_origin(None, env=env) == 'https://env.example'
    assert resolve_origin(None, env={}) == DEFAULT_ORIGIN


@pytest.mark.parametrize('value', [
    'http://mirror.example',
    'https://user@mirror.example',
    'https://user:pass@mirror.example',
    'https://mirror.example/path',
    'https://mirror.example/?x=1',
    'https://mirror.example/#frag',
    'https://',
    'https://mirror example',
    'https://mirror.example:0',
    'https://mirror.example:99999',
    'https://mirror.example:abc',
    'not-a-url',
    '',
])
def test_invalid_origins_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_origin(value)


def test_http_is_only_allowed_for_explicit_loopback():
    assert normalize_origin('http://127.0.0.1:8000',
                            allow_http_loopback=True) == 'http://127.0.0.1:8000'
    with pytest.raises(ValueError):
        normalize_origin('http://mirror.example', allow_http_loopback=True)


def test_fetch_select_uses_custom_origin():
    url, digest, size = fetch.select(BENCH_ASSETS, 'pool-public-smoke-test',
                                     'https://mirror.example:8443')
    assert url == ('https://mirror.example:8443'
                   '/submissions/originals/pool-public-smoke-test.jpg')
    assert len(digest) == 64
    assert size == 1778514


def test_fetch_select_rejects_invalid_origin():
    with pytest.raises(ValueError):
        fetch.select(BENCH_ASSETS, 'pool-public-smoke-test',
                     'http://mirror.example')


def test_fetch_cli_passes_origin(monkeypatch):
    captured = {}

    def fake_select(assets, asset_id, origin):
        captured['origin'] = origin
        return 'https://mirror.example/x.jpg', 'a' * 64, 1024

    monkeypatch.setattr(fetch, 'select', fake_select)
    monkeypatch.setattr(fetch, 'download', lambda *a: 'single')
    rc = fetch.main(['asset', 'out.bin', '--assets', 'any.yaml',
                     '--origin', 'https://mirror.example'])
    assert rc == 0
    assert captured['origin'] == 'https://mirror.example'


def test_transport_accepts_custom_https_host():
    path, host, port = rt._target('https://mirror.example:8443/a/b.jpg')
    assert (path, host, port) == ('/a/b.jpg', 'mirror.example', 8443)
    with pytest.raises(ValueError):
        rt._target('https://user:pass@mirror.example/a.jpg')
    with pytest.raises(ValueError):
        rt._target('http://mirror.example/a.jpg')


def test_transport_connections_use_custom_endpoint(monkeypatch):
    seen = []

    class FakeConn:
        def __init__(self, *args, **kwargs):
            seen.append((args, kwargs))

    monkeypatch.setattr(rt.http.client, 'HTTPSConnection', FakeConn)
    endpoint = rt._Endpoint('mirror.example', 8443, ssl.create_default_context())
    rt._open_conn(endpoint)
    assert seen[0][0] == ('mirror.example', 8443)
