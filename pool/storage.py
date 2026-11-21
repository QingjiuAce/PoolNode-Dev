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
    pass


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
    - 发布后设为 0444（只读），防止运行期被改写。
    """
    final = resolve_safe(cfg.storage_root, relative_path)
    final.parent.mkdir(parents=True, exist_ok=True)

    tmp_dev = os.stat(tmp_path).st_dev
    root_dev = os.stat(cfg.storage_root).st_dev if cfg.storage_root.exists() else tmp_dev

    if tmp_dev == root_dev:
        if final.exists():
            # Windows 上 os.replace 不能覆盖只读文件；POSIX 上此调用无害
            os.chmod(final, 0o644)
        os.replace(tmp_path, final)
    else:
        stage = final.parent / f".{final.name}.staging-{os.getpid()}"
        with open(tmp_path, "rb") as src, open(stage, "wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if final.exists():
            os.chmod(final, 0o644)
        os.replace(stage, final)
        tmp_path.unlink(missing_ok=True)

    os.chmod(final, 0o444)
    fsync_dir(final.parent)
    return final


def stored_path(cfg: PoolConfig, relative_path: str) -> pathlib.Path:
    return resolve_safe(cfg.storage_root, relative_path)


def remove_file(cfg: PoolConfig, relative_path: str) -> pathlib.Path:
    """删除白名单资产对应的物理文件。

    只接受能通过安全解析的路径；拒绝目录、符号链接与不存在的文件。
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
    """追加一条 JSONL 操作记录（时间戳自动补全）。"""
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
