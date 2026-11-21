"""pool-poc 命令行入口。

用法：
    pool-poc --config /etc/pool-poc/config.yaml check-upstream <asset_id>
    pool-poc --config /etc/pool-poc/config.yaml sync <asset_id>
    pool-poc --config /etc/pool-poc/config.yaml verify  <asset_id>
    pool-poc --config /etc/pool-poc/config.yaml remove  <asset_id>

CLI 负责串联流程、呈现结果与映射退出码；授权、网络、文件检查和
落盘分别交给 permissions、downloader、validator 和 storage。
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, PoolConfig, load_config
from .downloader import (
    UpstreamError,
    check_upstream,
    download_to_file,
)
from .permissions import (
    Asset,
    AssetConfigError,
    PermissionError,
    check_permission,
    get_asset,
    load_assets,
    validate_source_url,
)
from .storage import (
    FilesystemError,
    StorageError,
    audit_writer,
    guarded_staging,
    SystemDiskGuardError,
    check_system_disk,
    last_record_for,
    new_tmp_file,
    publish,
    record,
    remove_file,
    stored_path,
)
from .validator import (
    FormatError,
    PathError,
    check_format,
    resolve_safe,
    sha256_file,
)

DEFAULT_CONFIG = "/etc/pool-poc/config.yaml"


def _load_all(config_path: str) -> tuple[PoolConfig, list[Asset]]:
    cfg = load_config(config_path)
    assets_path = os.environ.get("POOL_POC_ASSETS", str(Path(config_path).parent / "assets.yaml"))
    assets = load_assets(assets_path)
    return cfg, assets


# ---------------------------------------------------------------- check-upstream

def cmd_check_upstream(cfg: PoolConfig, assets: list[Asset], asset_id: str) -> int:
    """上游连通性诊断。不保存任何字节到公开目录。"""
    asset = get_asset(assets, asset_id)
    validate_source_url(asset, cfg.upstream.allowed_hosts, cfg.upstream.allow_loopback_http)

    print(f"asset_id        : {asset.asset_id}")
    print(f"permission      : {asset.permission_status} (evidence: {'yes' if asset.permission_evidence else 'NO'})")
    print(f"source_url      : {asset.source_url}")
    print(f"relative_path   : {asset.relative_path}")
    print("-" * 64)

    result = check_upstream(asset.source_url, cfg.upstream)
    dns = result["dns"]
    print(f"DNS             : OK {dns['addresses']} ({dns['ms']} ms)")
    print(f"TCP             : OK -> {result['tcp']['peer']} ({result['tcp']['ms']} ms)")
    tls = result["tls"]
    if tls["ok"]:
        print(f"TLS             : OK {tls['version']} / {tls['cipher']} ({tls['ms']} ms)")
    else:
        print(f"TLS             : {tls.get('note', 'skipped')}")
    head = result["head"]
    if head.get("ok"):
        print(f"HEAD            : HTTP {head['status']} type={head['content_type']} len={head['content_length']}")
    else:
        print(f"HEAD            : 不支持/失败 ({head.get('error', 'unknown')})")
    get = result["get"]
    print(f"GET             : HTTP {get['status']} type={result['http']['content_type']} "
          f"len={result['http']['content_length']}")
    print(f"TTFB            : {get['ttfb_ms']} ms")
    print(f"取样下载        : {get['sample_bytes']} bytes / {get['total_ms']} ms "
          f"~ {int(get['sample_speed_bps'] or 0) / 1024:.0f} KB/s")
    print(f"图片特征        : {'是' if get['sample_is_image_magic'] else '否（非图片 magic bytes）'}")
    return 0


def _check_storage_capacity(cfg: PoolConfig) -> None:
    # 存储根可能尚未创建；从最近存在的祖先确认目标文件系统。
    try:
        probe = cfg.storage_root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        free = shutil.disk_usage(probe).free
    except (OSError, RuntimeError, ValueError) as exc:
        if not cfg.guard_system_disk:
            raise
        raise SystemDiskGuardError("无法确认存储盘余量，拒绝同步（guard_system_disk）") from exc
    if free < cfg.min_free_space:
        raise StorageError(
            f"存储根剩余空间 {free} 字节低于安全余量 {cfg.min_free_space}，"
            f"拒绝同步（数据盘保留安全余量）"
        )
    print(f"[3/8] 磁盘余量      : OK（剩余 {free // (1024 * 1024)} MB）")


def cmd_sync(cfg: PoolConfig, assets: list[Asset], asset_id: str) -> int:
    """先授权，再暂存和校验单个资产，最后发布。"""
    asset: Asset | None = None
    tmp_path: Path | None = None
    resources = ExitStack()
    published = False
    try:
        asset = get_asset(assets, asset_id)
        # 先拒绝未授权资产，避免产生网络请求或临时文件。
        check_permission(asset)
        print(f"[1/8] 授权检查      : OK（{asset.permission_status}，证据已登记）")

        # 5. 上游 URL 关
        validate_source_url(asset, cfg.upstream.allowed_hosts, cfg.upstream.allow_loopback_http)
        print(f"[2/8] 上游 URL 校验 : OK（{asset.source_url}）")

        # 6. 路径关 + 磁盘余量护栏
        try:
            target = resolve_safe(cfg.storage_root, asset.relative_path)
        except (OSError, RuntimeError, ValueError) as exc:
            if not cfg.guard_system_disk:
                raise
            raise SystemDiskGuardError("无法解析同步目标，拒绝同步（guard_system_disk）") from exc
        check_system_disk(cfg, target)
        if target.exists():
            existing = sha256_file(target)
            print(f"[3/8] 路径校验      : OK（目标已存在，sha256={existing[:16]}…，将按版本策略覆盖）")
        else:
            print(f"[3/8] 路径校验      : OK（新文件）")
        _check_storage_capacity(cfg)
        if cfg.guard_system_disk:
            print("[3/8] 数据盘挂载护栏: OK（独立设备）")

        # 在公开目录外暂存，避免让不完整的下载被对外提供。
        if cfg.guard_system_disk:
            # Check the real audit file before any staging or download.
            try:
                with audit_writer(cfg, preflight=True):
                    pass
            except StorageError as exc:
                raise SystemDiskGuardError(f"审计预检未通过: {exc}") from exc
            stage = resources.enter_context(guarded_staging(cfg, asset.asset_id))
            destination = stage.stream
        else:
            tmp_path = new_tmp_file(cfg, asset.asset_id)
            stage = destination = tmp_path
        _, content_type, size = download_to_file(
            asset.source_url, cfg.upstream, destination, cfg.max_file_size
        )
        print(f"[4/8] 流式下载      : OK {size} bytes（Content-Type: {content_type or '未知'}）")

        # 8. 大小关
        if size == 0:
            raise StorageError("下载内容为 0 字节")
        if size > cfg.max_file_size:
            raise StorageError(f"文件 {size} 超过上限 {cfg.max_file_size}")
        print(f"[5/8] 大小检查      : OK（{size} <= {cfg.max_file_size}）")

        # 9. 真实格式关（magic bytes，不信任扩展名与 Content-Type）
        if cfg.guard_system_disk:
            destination.seek(0)
        fmt = check_format(destination, cfg.allowed_formats)
        print(f"[6/8] 格式检查      : OK（实测 {fmt}）")

        # 10-11. SHA256 关
        if cfg.guard_system_disk:
            destination.seek(0)
        local_sha = sha256_file(destination)
        if asset.expected_sha256:
            if local_sha != asset.expected_sha256:
                raise StorageError(
                    f"SHA256 不一致：本地 {local_sha} ≠ 官方登记 {asset.expected_sha256}，拒绝发布"
                )
            hash_source = "official"
            print(f"[7/8] SHA256 校验   : OK（与白名单官方登记值一致）")
        else:
            hash_source = "local"
            print(f"[7/8] SHA256 校验   : 降级——白名单未登记官方 SHA256，"
                  f"本地计算 {local_sha}（不视为完成官方一致性验证）")

        # 12. 原子发布
        final = publish(cfg, stage, asset.relative_path)
        published = True
        tmp_path = None  # publish 已转移所有权
        print(f"[8/8] 原子发布      : OK -> {final}")

        # 13. 同步记录
        record(cfg, {
            "action": "sync",
            "asset_id": asset.asset_id,
            "revision": asset.revision,
            "relative_path": asset.relative_path,
            "sha256": local_sha,
            "hash_source": hash_source,
            "format": fmt,
            "size": size,
            "source_url": asset.source_url,
            "result": "published",
        })
        print(f"\nDONE sha256={local_sha}")
        return 0
    except SystemDiskGuardError as e:
        # The audit directory may be on the same unmounted volume as the data.
        if published:
            print("文件已发布，但审计未完成", file=sys.stderr)
        print(f"\nFAIL: {e}", file=sys.stderr)
        return 1
    except (PermissionError, PathError, UpstreamError, FormatError, StorageError, OSError) as e:
        if isinstance(e, OSError):
            e = FilesystemError("同步文件操作", e)
        print(f"\nFAIL: {e}", file=sys.stderr)
        if published:
            print("文件已发布，但审计未完成", file=sys.stderr)
            return 1
        reason = getattr(e, "reason", None) or getattr(e, "kind", None) or "sync_failed"
        try:
            record(cfg, {
                "action": "sync",
                "asset_id": asset_id,
                "revision": asset.revision if asset else None,
                "relative_path": asset.relative_path if asset else None,
                "result": "rejected",
                "reason": str(reason),
                "detail": str(e),
            })
        except (StorageError, OSError) as audit_error:
            print(f"审计未写入: {audit_error}", file=sys.stderr)
        return 1
    finally:
        try:
            resources.close()
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            print(FilesystemError("清理暂存文件", exc), file=sys.stderr)



# ---------------------------------------------------------------- verify

def cmd_verify(cfg: PoolConfig, assets: list[Asset], asset_id: str) -> int:
    """校验已发布文件：存在性、大小、SHA256（与白名单/同步记录比对）。"""
    asset = get_asset(assets, asset_id)
    target = stored_path(cfg, asset.relative_path)

    if not target.exists():
        print(f"NOT_PRESENT: {target} 不存在（未同步或已删除）")
        return 1

    actual = sha256_file(target)
    size = target.stat().st_size
    print(f"file            : {target}")
    print(f"size            : {size} bytes")
    print(f"sha256          : {actual}")

    ok = True
    if asset.expected_sha256:
        match = actual == asset.expected_sha256
        print(f"expected(官方)  : {asset.expected_sha256} -> {'MATCH' if match else 'MISMATCH'}")
        ok &= match
    else:
        print(f"expected(官方)  : 未登记（无法做官方一致性验证）")

    last = last_record_for(cfg, asset.asset_id, "sync")
    if last and last.get("result") == "published":
        match = actual == last["sha256"]
        print(f"last_sync 记录  : {last['sha256']} ({last['hash_source']}) -> "
              f"{'MATCH' if match else 'MISMATCH'}")
        ok &= match
    else:
        print("last_sync 记录  : 无已发布记录")

    try:
        fmt = check_format(target, cfg.allowed_formats)
        print(f"format          : {fmt}")
    except FormatError as e:
        print(f"format          : FAIL: {e}")
        print("RESULT          : FAIL")
        return 1
    print("RESULT          : " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------- remove

def cmd_remove(cfg: PoolConfig, assets: list[Asset], asset_id: str) -> int:
    """删除白名单资产对应的物理文件并留痕。"""
    try:
        asset = get_asset(assets, asset_id)
    except PermissionError as e:
        print(f"DENIED [{e.reason}]: {e}", file=sys.stderr)
        return 1
    removed = None
    try:
        with audit_writer(cfg) as (append, verify_audit):
            try:
                removed = remove_file(cfg, asset.relative_path, before_unlink=verify_audit)
            except (StorageError, PathError, OSError) as error:
                print(f"FAIL: {error}", file=sys.stderr)
                if not isinstance(error, SystemDiskGuardError):
                    append({
                        "action": "remove", "asset_id": asset.asset_id,
                        "relative_path": asset.relative_path, "result": "error",
                        "detail": str(error),
                    })
                return 1
            append({
                "action": "remove", "asset_id": asset.asset_id, "revision": asset.revision,
                "relative_path": asset.relative_path, "removed_path": str(removed),
                "result": "removed",
            })
    except (StorageError, OSError) as exc:
        if removed is not None:
            print("文件已删除，但审计未完成", file=sys.stderr)
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"REMOVED: {removed}")
    print("对外下载路径已失效（Nginx 将返回 404）。")
    return 0


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pool-poc",
        description="Pool PoC V0.1 —— 独立备用原图下载节点管理工具",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--version", action="version", version=f"pool-poc {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in [
        ("check-upstream", "上游连通性诊断（不落盘）"),
        ("sync", "同步单个资产（授权→下载→校验→原子发布）"),
        ("verify", "校验已发布文件完整性"),
        ("remove", "删除白名单资产对应的物理文件"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("asset_id", help="白名单中的 asset_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg, assets = _load_all(args.config)
    except (ConfigError, AssetConfigError) as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2

    dispatch = {
        "check-upstream": cmd_check_upstream,
        "sync": cmd_sync,
        "verify": cmd_verify,
        "remove": cmd_remove,
    }
    try:
        return dispatch[args.command](cfg, assets, args.asset_id)
    except PermissionError as e:
        print(f"DENIED [{e.reason}]: {e}", file=sys.stderr)
        return 1
    except (PathError, UpstreamError, FormatError, StorageError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
