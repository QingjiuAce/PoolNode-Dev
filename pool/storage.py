"""存储层：临时区、原子发布、删除、同步记录。

原则：任何校验失败都不能在公开目录留下不完整文件；
发布 = 临时目录下载 + 校验 + 同文件系统 os.replace()。
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time

from .config import PoolConfig
from .validator import fsync_dir, resolve_safe


class StorageError(Exception):
    """存储操作拒绝了目标路径或文件状态。"""


def _replace_public_file(source: pathlib.Path, destination: pathlib.Path) -> None:
    """必要时先恢复写权限，以便在 Windows 上覆盖已有的只读文件。"""
    if destination.exists():
        os.chmod(destination, 0o644)
    os.replace(source, destination)


def ensure_dirs(cfg: PoolConfig) -> None:
    """创建临时区与日志目录（不创建公开存储根，由部署脚本建好并授权）。"""
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    cfg.records_file.parent.mkdir(parents=True, exist_ok=True)


def new_tmp_file(cfg: PoolConfig, asset_id: str) -> pathlib.Path:
    """在隔离的临时目录创建唯一临时文件。"""
    ensure_dirs(cfg)
    fd, name = tempfile.mkstemp(prefix=f"{asset_id}.", suffix=".part", dir=cfg.tmp_dir)
    os.close(fd)
    return pathlib.Path(name)


def publish(cfg: PoolConfig, tmp_path: pathlib.Path, relative_path: str) -> pathlib.Path:
    """把临时文件原子移动到公开存储区。

    - 目标路径经 resolve_safe 双层校验；
    - 同一文件系统直接 os.replace()；跨文件系统退化为“写 .staging + fsync + replace”；
    - 发布后设为 0444（只读），避免运行期意外改写。
    """
    final = resolve_safe(cfg.storage_root, relative_path)
    final.parent.mkdir(parents=True, exist_ok=True)

    tmp_dev = os.stat(tmp_path).st_dev
    root_dev = os.stat(cfg.storage_root).st_dev if cfg.storage_root.exists() else tmp_dev

    if tmp_dev == root_dev:
        _replace_public_file(tmp_path, final)
    else:
        # 先在目标文件系统写完并同步，再替换公开文件名。
        stage = final.parent / f".{final.name}.staging-{os.getpid()}"
        with open(tmp_path, "rb") as src, open(stage, "wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        _replace_public_file(stage, final)
        tmp_path.unlink(missing_ok=True)

    os.chmod(final, 0o444)
    fsync_dir(final.parent)
    return final


def stored_path(cfg: PoolConfig, relative_path: str) -> pathlib.Path:
    """返回相对路径在公开存储根中解析出的路径，不检查文件是否存在。"""
    return resolve_safe(cfg.storage_root, relative_path)


def remove_file(cfg: PoolConfig, relative_path: str) -> pathlib.Path:
    """删除白名单资产对应的物理文件。

    目标必须通过安全解析；随后在解析结果上检查符号链接、存在性与目录状态。
    """
    target = resolve_safe(cfg.storage_root, relative_path)
    if target.is_symlink():
        raise StorageError(f"目标是符号链接，拒绝删除: {target}")
    if not target.exists():
        raise StorageError(f"文件不存在（可能已删除）: {target}")
    if target.is_dir():
        raise StorageError(f"目标是目录，拒绝删除: {target}")
    try:
        target.unlink()
    except PermissionError:
        # Windows 上只读文件必须先去掉只读位才能删除；POSIX 上不会走到这里
        os.chmod(target, 0o644)
        target.unlink()
    fsync_dir(target.parent)
    return target


def record(cfg: PoolConfig, event: dict) -> None:
    """追加一条 JSONL 操作记录；调用方未提供时间戳时自动补全。"""
    ensure_dirs(cfg)
    event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
    with open(cfg.records_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def read_records(cfg: PoolConfig) -> list[dict]:
    if not cfg.records_file.exists():
        return []
    out = []
    for line in cfg.records_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def last_record_for(cfg: PoolConfig, asset_id: str, action: str) -> dict | None:
    for ev in reversed(read_records(cfg)):
        if ev.get("asset_id") == asset_id and ev.get("action") == action:
            return ev
    return None
