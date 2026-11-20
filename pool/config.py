"""配置加载与校验。

生产配置位于 /etc/pool-poc/config.yaml（root:pool 0640）；
本地测试通过 --config 指向临时配置。
"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any

import yaml

# 允许发布的真实图片格式（magic bytes 判定，SVG 不在列表中即被拒绝）
KNOWN_FORMATS = frozenset({"jpeg", "png", "gif", "webp"})


class ConfigError(Exception):
    """配置缺失、非法或语义不合法。"""


@dataclasses.dataclass(frozen=True)
class UpstreamConfig:
    allowed_hosts: tuple[str, ...]
    connect_timeout: int = 10
    read_timeout: int = 60
    # 仅供本地/CI 测试使用：允许对回环地址走 http://。生产配置严禁开启。
    allow_loopback_http: bool = False


@dataclasses.dataclass(frozen=True)
class PoolConfig:
    storage_root: pathlib.Path
    tmp_dir: pathlib.Path
    log_dir: pathlib.Path
    records_file: pathlib.Path
    max_file_size: int  # 单文件字节上限
    min_free_space: int  # 存储根所在盘的最小剩余空间（数据盘安全余量护栏）
    guard_system_disk: bool  # true: storage_root 与系统盘同设备时拒绝同步（防数据盘掉挂载误写）
    allowed_formats: frozenset[str]
    upstream: UpstreamConfig


def _require(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ConfigError(f"缺少必需配置项: {key}")
    return data[key]


def load_config(path: str | pathlib.Path) -> PoolConfig:
    """读取并校验配置文件。任何语义错误都在启动时立即失败。"""
    p = pathlib.Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"配置文件不存在: {p}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 YAML 解析失败: {p}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件顶层必须是映射: {p}")

    storage_root = pathlib.Path(str(_require(raw, "storage_root")))
    if not storage_root.is_absolute():
        raise ConfigError(f"storage_root 必须是绝对路径: {storage_root}")
    tmp_dir = pathlib.Path(str(_require(raw, "tmp_dir")))
    if not tmp_dir.is_absolute():
        raise ConfigError(f"tmp_dir 必须是绝对路径: {tmp_dir}")
    if tmp_dir == storage_root or storage_root in tmp_dir.parents:
        raise ConfigError("tmp_dir 不得位于 storage_root 内部（临时区必须与公开区隔离）")

    log_dir = pathlib.Path(str(_require(raw, "log_dir")))
    records_file = pathlib.Path(str(_require(raw, "records_file")))

    max_mb = float(_require(raw, "max_file_size_mb"))
    if not (0 < max_mb <= 1024):
        raise ConfigError(f"max_file_size_mb 必须在 (0, 1024] 内: {max_mb}")

    formats = set(_require(raw, "allowed_formats"))
    unknown = formats - KNOWN_FORMATS
    if unknown or not formats:
        raise ConfigError(
            f"allowed_formats 只能取 {sorted(KNOWN_FORMATS)}，收到: {sorted(formats) or '(空)'}"
        )

    min_free_mb = int(raw.get("min_free_space_mb", 1024))
    if not (0 < min_free_mb <= 50 * 1024):
        raise ConfigError(f"min_free_space_mb 必须在 (0, 51200] 内: {min_free_mb}")

    upstream_raw = _require(raw, "upstream")
    if not isinstance(upstream_raw, dict):
        raise ConfigError("upstream 必须是映射")
    hosts_raw = _require(upstream_raw, "allowed_hosts")
    if not isinstance(hosts_raw, list) or not hosts_raw:
        raise ConfigError("upstream.allowed_hosts 必须是非空列表")
    hosts = tuple(str(h).strip().lower().rstrip(".") for h in hosts_raw)
    if "" in hosts or any(" " in h or "/" in h for h in hosts):
        raise ConfigError(f"upstream.allowed_hosts 含非法主机名: {hosts}")

    upstream = UpstreamConfig(
        allowed_hosts=hosts,
        connect_timeout=int(upstream_raw.get("connect_timeout", 10)),
        read_timeout=int(upstream_raw.get("read_timeout", 60)),
        allow_loopback_http=bool(upstream_raw.get("allow_loopback_http", False)),
    )

    return PoolConfig(
        storage_root=storage_root,
        tmp_dir=tmp_dir,
        log_dir=log_dir,
        records_file=records_file,
        max_file_size=int(max_mb * 1024 * 1024),
        min_free_space=min_free_mb * 1024 * 1024,
        guard_system_disk=bool(raw.get("guard_system_disk", False)),
        allowed_formats=frozenset(formats),
        upstream=upstream,
    )
