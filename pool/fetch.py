"""Opt-in end-user download client. Independent of the server-side sync CLI."""
from __future__ import annotations

import argparse
import http.client
import json
import pathlib
import sys
import time

from . import permissions, validator
from .range_transport import Metrics, download

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
    parser.add_argument('--metrics', type=pathlib.Path,
                        help='把逐请求阶段计时（DNS/TCP/TLS/TTFB/Body）写入该 JSONL 文件')
    parser.add_argument('--scheduler', choices=('static', 'dynamic', 'auto'), default='auto',
                        help='auto（默认）=≥4MiB 用 dynamic 任务队列、小文件保持 static 固定等分；'
                             'static=v0.4 行为；dynamic=强制任务队列')
    parser.add_argument('--run-id',
                        help='实验运行 ID（默认自动生成）；写入本次运行的每条 metrics 记录')
    args = parser.parse_args(argv)
    metrics = Metrics(run_id=args.run_id) if args.metrics is not None else None
    dumped = False
    try:
        if args.metrics is not None and args.metrics.exists():
            # Fail before any network or filesystem work: an existing metrics
            # file is a different run, and silently replacing it destroys the
            # only copy of that run's diagnostics.
            raise FileExistsError(f'metrics 文件已存在，拒绝覆盖: {args.metrics}')
        url, digest, registered = select(args.assets, args.asset_id)
        if registered is not None and args.size is not None and registered != args.size:
            raise ValueError('--size 与登记字节数不一致')
        size = registered if registered is not None else args.size
        if size is None or not 1 <= size <= MAX_BYTES:
            raise ValueError('需要 1 B 至 20 MiB 的准确字节数（size_bytes 或 --size）')
        workers = args.workers if args.workers is not None else (
            1 if size < 512 * 1024 else 4 if size < 2 * 1024**2 else 16)
        scheduler = args.scheduler
        if scheduler == 'auto':
            # measured crossover (2026-11-23 paired runs): the chunk queue wins
            # on large files at high worker counts, static wins on small files
            scheduler = 'dynamic' if size >= 4 * 1024 * 1024 else 'static'
        t0 = time.perf_counter()
        mode = download(url, size, digest, args.output, workers, metrics,
                        scheduler)
        wall = time.perf_counter() - t0
        print(f'已校验 SHA256：{digest}；模式：{mode}；输出：{args.output}')
        if metrics is not None:
            metrics.add({'kind': 'download', 'ts': round(time.time(), 3),
                         'wall_ms': round(wall * 1000, 2), 'mode': mode,
                         'scheduler': scheduler, 'size': size,
                         'workers': workers, 'error': None})
            metrics.dump(args.metrics)
            dumped = True
        return 0
    except BaseException as exc:
        if metrics is not None and not dumped:
            metrics.add({'kind': 'download', 'ts': round(time.time(), 3),
                         'wall_ms': None, 'mode': None, 'size': None,
                         'workers': None, 'error': exc.__class__.__name__})
            try:
                metrics.dump(args.metrics)
            except FileExistsError as dump_exc:
                print(f'metrics 未写入：{dump_exc}', file=sys.stderr)
        if isinstance(exc, (permissions.AssetConfigError, permissions.PermissionError,
                            validator.PathError, ValueError, OSError, RuntimeError,
                            http.client.HTTPException)):
            print(f'下载失败：{exc}', file=sys.stderr)
            return 1
        raise


if __name__ == '__main__':
    raise SystemExit(main())
