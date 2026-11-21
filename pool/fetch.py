"""Opt-in end-user download client. Independent of the server-side sync CLI."""
from __future__ import annotations

import argparse
import http.client
import pathlib
import sys

from . import permissions, validator
from .range_transport import download

ORIGIN = 'https://dl.bszx.site'
MAX_BYTES = 20 * 1024 * 1024  # v1 product limit, independent of server config


def select(assets_path: pathlib.Path, asset_id: str) -> tuple[str, str, int | None]:
    asset = permissions.get_asset(permissions.load_assets(assets_path), asset_id)
    permissions.check_permission(asset)
    rel = validator.validate_relative_path(asset.relative_path)
    if not asset.expected_sha256:
        raise ValueError('缺少登记的 expected_sha256，拒绝下载')
    return f'{ORIGIN}/{rel}', asset.expected_sha256, asset.size_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='从 Pool 下载已授权且登记 SHA256 的文件')
    parser.add_argument('asset_id', help='受信白名单中的资产 ID，不接受 URL')
    parser.add_argument('output', type=pathlib.Path, help='新的本地文件名；父目录必须已存在')
    parser.add_argument('--assets', required=True, type=pathlib.Path, help='维护者提供的受信资产白名单')
    parser.add_argument('--size', type=int, help='旧清单缺少 size_bytes 时提供准确字节数')
    parser.add_argument('--workers', type=int, choices=range(1, 17), metavar='1..16',
                        help='并发上限；1 强制使用单链接')
    args = parser.parse_args(argv)
    try:
        url, digest, registered = select(args.assets, args.asset_id)
        if registered is not None and args.size is not None and registered != args.size:
            raise ValueError('--size 与登记字节数不一致')
        size = registered if registered is not None else args.size
        if size is None or not 1 <= size <= MAX_BYTES:
            raise ValueError('需要 1 B 至 20 MiB 的准确字节数（size_bytes 或 --size）')
        workers = args.workers if args.workers is not None else (
            1 if size < 512 * 1024 else 4 if size < 2 * 1024**2 else 16)
        mode = download(url, size, digest, args.output, workers)
    except (permissions.AssetConfigError, permissions.PermissionError, validator.PathError,
            ValueError, OSError, RuntimeError, http.client.HTTPException) as exc:
        print(f'下载失败：{exc}', file=sys.stderr)
        return 1
    print(f'已校验 SHA256：{digest}；模式：{mode}；输出：{args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
