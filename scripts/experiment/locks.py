#!/usr/bin/env python3
"""跨进程资源互斥（PN-EXPERIMENT-01 阶段 1 / §2.4）。

设计要点
--------
* **非阻塞**：拿不到锁立刻放弃（不排队、不延后），由调用方记为 SKIPPED。
* **OS 级锁**：Windows 用 ``msvcrt.locking``，POSIX 用 ``fcntl.flock``。
  进程异常退出时由内核自动释放，因此**不依赖锁文件年龄**判断是否失效，
  也不会留下永久残留锁（§2.4 第 9、10 条）。
* **持有者元数据**：锁对象与元数据分离（``<resource>.lock`` + ``<resource>.owner.json``）。
  冲突时读取元数据用于报告；元数据可能缺失（持有者刚启动或已被杀），
  此时如实记为 ``unknown``，不做任何推断。
* **固定加锁顺序**：按 resource_id 排序后依次获取，且全部非阻塞，
  因此不存在环路等待（无死锁）。
* **幂等释放**：释放时逐个解锁并清空，重复调用安全。
* **冲突留痕**：每次 获取/冲突/释放 都追加到 ``locks.jsonl``，
  与正式实验样本（runs.jsonl）完全分离（§2.4 第 6 条）。

资源命名（只允许小写字母、数字与 ``:._-``）::

    link:local-origin     本机到香港 origin 的链路（所有下载类实验）
    out:cmin-experiment   输出目录
    ssh:cn2               CN2 探测机的 SSH 通道

用法::

    from locks import hold, LockConflict
    try:
        with hold(["link:local-origin", "out:cmin-experiment"], "cmin", run_id):
            run()
    except LockConflict as exc:
        # 记 SKIPPED，原因写 exc.reason()
        ...
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import platform
import re
import secrets

RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,63}$")


class LockError(RuntimeError):
    """锁系统自身故障（权限、目录不可写等），不是竞争。"""


class LockConflict(RuntimeError):
    """资源被其他进程持有。"""

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        super().__init__(self.reason())

    def reason(self) -> str:
        parts = []
        for item in self.conflicts:
            holder = item.get("holder") or {}
            ident = "/".join(str(holder.get(k) or "?") for k in ("task", "run_id"))
            parts.append(f"{item['resource']} held_by {ident} pid={holder.get('pid', '?')}")
        return "lock_conflict: " + "; ".join(parts)


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _slug(resource: str) -> str:
    if not RESOURCE_RE.match(resource):
        raise LockError(f"invalid resource id: {resource!r}")
    return resource.replace(":", "_")


def _append_journal(path: pathlib.Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _try_lock(fh) -> bool:
    """非阻塞独占锁；成功返回 True，被占用返回 False。"""
    fh.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        # 进程将退出/描述符已关闭时解锁失败不影响正确性：内核会释放
        pass


def _read_owner(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class LockSet:
    """一组资源的独占锁，按排序顺序获取，退出时逆序释放。"""

    def __init__(self, lock_dir: pathlib.Path, journal: pathlib.Path):
        self.lock_dir = pathlib.Path(lock_dir)
        self.journal = pathlib.Path(journal)
        self._held: list[tuple[str, object, dict]] = []
        self.records: list[dict] = []

    def acquire(self, resources: list[str], task: str, run_id: str) -> "LockSet":
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        conflicts: list[dict] = []
        for resource in sorted(set(resources)):
            slug = _slug(resource)
            lock_path = self.lock_dir / f"{slug}.lock"
            owner_path = self.lock_dir / f"{slug}.owner.json"
            try:
                fh = open(lock_path, "a+b")
                if fh.tell() == 0:
                    fh.write(b"0")
                    fh.flush()
            except OSError as exc:
                self.release()
                raise LockError(f"lock_open_failed:{resource}:{exc}") from exc
            if not _try_lock(fh):
                fh.close()
                holder = _read_owner(owner_path)
                conflicts.append({"resource": resource, "holder": holder,
                                  "holder_metadata": "present" if holder else "unknown"})
                continue
            record = {"lock_id": secrets.token_hex(6), "resource_id": resource,
                      "task": task, "run_id": run_id, "pid": os.getpid(),
                      "host": platform.node(), "acquired_at": _now().isoformat(),
                      "state": "acquired"}
            try:
                tmp = owner_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(record, ensure_ascii=False) + "\n",
                               encoding="utf-8")
                tmp.replace(owner_path)
            except OSError as exc:
                _unlock(fh)
                fh.close()
                self.release()
                raise LockError(f"owner_write_failed:{resource}:{exc}") from exc
            _append_journal(self.journal, record)
            self._held.append((resource, fh, record))
            self.records.append(record)
        if conflicts:
            self.release()
            for item in conflicts:
                _append_journal(self.journal, {
                    "lock_id": secrets.token_hex(6), "resource_id": item["resource"],
                    "task": task, "run_id": run_id, "pid": os.getpid(),
                    "host": platform.node(), "at": _now().isoformat(),
                    "state": "conflict", "holder": item["holder"],
                    "holder_metadata": item["holder_metadata"]})
            raise LockConflict(conflicts)
        return self

    def release(self) -> None:
        while self._held:
            resource, fh, record = self._held.pop()
            _unlock(fh)
            try:
                fh.close()
            except OSError:
                pass
            _append_journal(self.journal, {
                "lock_id": record["lock_id"], "resource_id": resource,
                "task": record.get("task"), "run_id": record.get("run_id"),
                "pid": os.getpid(), "host": platform.node(),
                "at": _now().isoformat(), "state": "released"})
        self.records.clear()

    def __enter__(self) -> "LockSet":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False

    def describe(self) -> list[dict]:
        return [{"resource_id": r, "lock_id": rec["lock_id"]}
                for r, fh, rec in self._held if not fh.closed]


def hold(lock_dir: pathlib.Path, journal: pathlib.Path, resources: list[str],
         task: str, run_id: str) -> LockSet:
    """获取一组资源锁；失败抛 LockConflict（竞争）或 LockError（系统故障）。"""
    return LockSet(lock_dir, journal).acquire(list(resources), task, run_id)
