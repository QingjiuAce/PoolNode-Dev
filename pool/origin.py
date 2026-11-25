"""Single place for the Pool HTTPS origin.

The manifest (`assets.yaml`) is the trust anchor: an asset_id maps to a
maintainer-registered relative path and SHA-256. The origin therefore selects
*where* to fetch, not *what* to trust — every byte is still verified against
the registered digest. Configuration must not weaken URL validation:

  * HTTPS only (HTTP is allowed only for explicit loopback test setups);
  * no credentials, path, query, or fragment in the origin;
  * host names must be ASCII and non-empty.

Resolution order: CLI --origin > POOL_ORIGIN environment > DEFAULT_ORIGIN.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

DEFAULT_ORIGIN = 'https://dl.bszx.site'
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


def normalize_origin(value: str, *, allow_http_loopback: bool = False) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError('origin 必须是非空 ASCII 字符串')
    parts = urlsplit(value)
    if parts.scheme not in ('https', 'http'):
        raise ValueError(f'origin 必须使用 https: {value}')
    if parts.username is not None or parts.password is not None:
        raise ValueError('origin 不允许包含凭据')
    if parts.path not in ('', '/') or parts.query or parts.fragment:
        raise ValueError(f'origin 只能包含 scheme://host[:port]: {value}')
    host = parts.hostname
    if not host:
        raise ValueError(f'origin 缺少主机名: {value}')
    if any(c.isspace() or c in '\\/?#@' for c in host):
        raise ValueError(f'origin 主机名非法: {value}')
    if parts.scheme == 'http' and not (
            allow_http_loopback and host.lower() in LOOPBACK_HOSTS):
        raise ValueError(f'origin 必须是 https: {value}')
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f'origin 端口非法: {value}') from exc
    if port is not None and not 0 < port < 65536:
        raise ValueError(f'origin 端口非法: {value}')
    netloc = host.lower().rstrip('.')
    if ':' in netloc:  # IPv6 literal
        netloc = f'[{netloc}]'
    if port is not None:
        netloc = f'{netloc}:{port}'
    return f'{parts.scheme}://{netloc}'


def resolve_origin(cli_value: str | None = None, *,
                   allow_http_loopback: bool = False,
                   env: dict[str, str] | None = None) -> str:
    raw = cli_value or (env if env is not None else os.environ).get('POOL_ORIGIN')
    return normalize_origin(raw or DEFAULT_ORIGIN,
                            allow_http_loopback=allow_http_loopback)
