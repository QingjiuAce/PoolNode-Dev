"""存储层：临时区、原子发布、删除、同步记录。

原则：任何校验失败都不能在公开目录留下不完整文件；
发布 = 临时目录下载 + 校验 + 同文件系统 os.replace()。
"""

from __future__ import annotations

import errno
import json
import os
import pathlib
import secrets
import stat
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Callable

from ._guarded_io import GuardedDirectory, directory, identity
from .config import PoolConfig
from .validator import fsync_dir, resolve_safe, validate_relative_path


class StorageError(Exception):
    """存储操作拒绝了目标路径或文件状态。"""


class SystemDiskGuardError(StorageError):
    """无法确认同步写入目标位于系统盘之外。"""


def _system_root() -> pathlib.Path:
    if os.name != "nt":
        return pathlib.Path("/")
    # Windows 上 '/' 是当前盘符，不一定是系统盘。不要信任环境变量。
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise OSError("cannot determine Windows system drive")
    windows_dir = pathlib.Path(buffer.value)
    if not windows_dir.is_absolute() or len(windows_dir.drive) != 2:
        raise OSError("invalid Windows system drive")
    return pathlib.Path(windows_dir.anchor)


def _existing_directory(path: pathlib.Path) -> pathlib.Path:
    """解析链接，检查最近存在的目录；仅在路径确实不存在时向上探测。"""
    probe = path.resolve()
    while True:
        try:
            mode = probe.stat().st_mode
        except FileNotFoundError:
            if probe == probe.parent:
                raise
            probe = probe.parent
            continue
        if not stat.S_ISDIR(mode):
            raise NotADirectoryError(str(probe))
        return probe


def _directory_device(path: pathlib.Path) -> int:
    return _existing_directory(path).stat().st_dev


def _check_write_devices(paths: dict[str, pathlib.Path]) -> None:
    try:
        system_device = _directory_device(_system_root())
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemDiskGuardError("无法确认系统盘设备，拒绝同步（guard_system_disk）") from exc
    for label, path in paths.items():
        try:
            device = _directory_device(path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemDiskGuardError(
                f"无法确认 {label} 所在设备，拒绝同步（guard_system_disk）"
            ) from exc
        if device == system_device:
            raise SystemDiskGuardError(
                f"{label} 与系统盘同设备：数据盘可能未挂载，"
                "拒绝同步以防误写系统盘（guard_system_disk）"
            )


def _audit_dirs(cfg: PoolConfig) -> dict[str, pathlib.Path]:
    return {
        "log_dir": cfg.log_dir,
        "records_file.parent": cfg.records_file.parent,
        "records_file": _audit_destination_parent(cfg),
    }


def _audit_destination_parent(cfg: PoolConfig) -> pathlib.Path:
    try:
        return cfg.records_file.resolve().parent
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemDiskGuardError("无法解析审计日志目标，拒绝同步（guard_system_disk）") from exc


def check_system_disk(cfg: PoolConfig, target: pathlib.Path) -> None:
    """在产生同步副作用前检查公开落点与暂存、审计位置。"""
    if cfg.guard_system_disk:
        try:
            storage_root = cfg.storage_root.resolve()
            tmp_dir = cfg.tmp_dir.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemDiskGuardError("无法解析公开或暂存目录，拒绝同步（guard_system_disk）") from exc
        if tmp_dir == storage_root or storage_root in tmp_dir.parents:
            raise SystemDiskGuardError("暂存目录解析后位于公开目录内，拒绝同步（guard_system_disk）")
        _check_write_devices({
            "storage_root": storage_root,
            "target.parent": target.parent,
            "tmp_dir": tmp_dir,
            **_audit_dirs(cfg),
        })


class FilesystemError(StorageError):
    def __init__(self, operation: str, error: OSError):
        self.errno = error.errno
        code = errno.errorcode.get(error.errno, "OSERROR")
        super().__init__(f"{operation}失败（{code}, errno={error.errno}）")


@dataclass
class StagedFile:
    parent: GuardedDirectory
    name: str
    stream: BinaryIO
    info: os.stat_result

    def verify(self) -> None:
        self.parent.verify_visible()
        if identity(self.parent.stat(self.name)) != identity(self.info):
            raise OSError(errno.ESTALE, "staging file changed")


def _check_file_info(info: os.stat_result, device: int) -> os.stat_result:
    if info.st_dev != device:
        raise OSError(errno.EXDEV, "unexpected opened file device")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError(errno.EINVAL, "expected an unlinked-to regular file")
    return info


@contextmanager
def guarded_staging(cfg: PoolConfig, asset_id: str):
    device = _directory_device(cfg.tmp_dir)
    if device == _directory_device(_system_root()):
        raise SystemDiskGuardError("tmp_dir 与系统盘同设备（guard_system_disk）")
    name = f"{asset_id}.{secrets.token_hex(12)}.part"
    with directory(cfg.tmp_dir, device, create=True, mode=0o700) as parent:
        fd = parent.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        info = None
        try:
            info = os.fstat(fd)
            _check_file_info(info, device)
            stage = StagedFile(parent, name, os.fdopen(fd, "w+b"), info)
            fd = None
            with stage.stream:
                stage.verify()
                yield stage
        finally:
            if fd is not None:
                os.close(fd)
            if info is not None:
                try:
                    parent.unlink(name, info, cleanup=True)
                except FileNotFoundError:
                    pass  # Already moved to the public name.
                except OSError as exc:
                    print(FilesystemError("清理暂存文件", exc), file=sys.stderr)
            else:
                print("无法核验暂存文件身份，未按路径清理", file=sys.stderr)


def _replace_public_file(source: pathlib.Path, destination: pathlib.Path) -> None:
    """必要时先恢复写权限，以便在 Windows 上覆盖已有的只读文件。"""
    if destination.exists():
        os.chmod(destination, 0o644)
    os.replace(source, destination)


def new_tmp_file(cfg: PoolConfig, asset_id: str) -> pathlib.Path:
    """在隔离的临时目录创建唯一临时文件。"""
    if cfg.guard_system_disk:
        raise SystemDiskGuardError("启用护栏时必须使用已核验的暂存描述符")
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{asset_id}.", suffix=".part", dir=cfg.tmp_dir)
    os.close(fd)
    return pathlib.Path(name)


def _publish_checked(cfg: PoolConfig, stage: StagedFile, relative_path: str):
    validate_relative_path(relative_path)
    final = cfg.storage_root / relative_path
    check_system_disk(cfg, final)
    device = _directory_device(cfg.storage_root)
    if device == _directory_device(_system_root()) or stage.info.st_dev != device:
        raise SystemDiskGuardError("暂存与公开存储必须位于同一非系统设备（guard_system_disk）")
    stage.verify()
    with directory(cfg.storage_root, device, create=True) as root:
        with directory(final.parent, device, create=True) as parent:
            try:
                previous = parent.stat(final.name)
            except FileNotFoundError:
                previous = None
            if previous is not None and (
                not stat.S_ISREG(previous.st_mode) or previous.st_nlink != 1
                or previous.st_dev != device
            ):
                raise OSError(errno.EINVAL, "unsafe publication target")
            if stage.parent.fd is not None:
                os.fchmod(stage.stream.fileno(), 0o444)
            else:
                # Windows generally cannot rename the file while its CRT handle is open.
                stage.stream.close()
                if previous is not None:
                    parent.chmod_windows(final.name, previous, 0o644)
            stage.verify()
            root.verify_visible()
            try:
                stage.parent.replace_to(stage.name, parent, final.name)
            except OSError:
                if previous is not None and parent.fd is None:
                    try:
                        parent.chmod_windows(final.name, previous, stat.S_IMODE(previous.st_mode))
                    except OSError as exc:
                        print(FilesystemError("恢复原文件权限", exc), file=sys.stderr)
                raise
            try:
                if parent.fd is None:
                    parent.chmod_windows(final.name, stage.info, 0o444)
                parent.verify_visible()
                if identity(parent.stat(final.name)) != identity(stage.info):
                    raise OSError(errno.ESTALE, "publication identity changed")
                parent.sync()
            except OSError as exc:
                raise FilesystemError("替换已执行，但发布状态核验", exc) from exc
    return final


def publish(cfg: PoolConfig, tmp_path: pathlib.Path | StagedFile, relative_path: str) -> pathlib.Path:
    """Publish verified bytes; only opt-out mode permits cross-device copying."""
    try:
        if not cfg.guard_system_disk:
            return _publish_legacy(cfg, tmp_path, relative_path)
        if isinstance(tmp_path, StagedFile):
            return _publish_checked(cfg, tmp_path, relative_path)
        device = _directory_device(cfg.tmp_dir)
        if tmp_path.parent != cfg.tmp_dir or device == _directory_device(_system_root()):
            raise SystemDiskGuardError("非法暂存位置（guard_system_disk）")
        with directory(tmp_path.parent, device) as parent:
            fd = parent.open(tmp_path.name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = _check_file_info(os.fstat(stream.fileno()), device)
                return _publish_checked(cfg, StagedFile(parent, tmp_path.name, stream, info), relative_path)
    except OSError as exc:
        raise FilesystemError("发布文件", exc) from exc


def _publish_legacy(cfg: PoolConfig, tmp_path: pathlib.Path, relative_path: str) -> pathlib.Path:
    """把临时文件原子移动到公开存储区。

    - 目标路径经 resolve_safe 双层校验；
    - 同一文件系统直接 os.replace()；跨文件系统退化为“写 .staging + fsync + replace”；
    - 发布后设为 0444（只读），避免运行期意外改写。
    """
    final = resolve_safe(cfg.storage_root, relative_path)
    final.parent.mkdir(parents=True, exist_ok=True)

    tmp_dev = os.stat(tmp_path).st_dev
    final_dev = final.parent.stat().st_dev

    if tmp_dev == final_dev:
        _replace_public_file(tmp_path, final)
    else:
        # 先在目标文件系统写完并同步，再替换公开文件名。
        fd, stage_name = tempfile.mkstemp(prefix=f".{final.name}.staging-", dir=final.parent)
        stage = pathlib.Path(stage_name)
        try:
            with os.fdopen(fd, "wb") as dst, open(tmp_path, "rb") as src:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            _replace_public_file(stage, final)
            tmp_path.unlink(missing_ok=True)
        finally:
            try:
                stage.unlink(missing_ok=True)
            except OSError as exc:
                print(FilesystemError("清理跨盘副本", exc), file=sys.stderr)

    os.chmod(final, 0o444)
    fsync_dir(final.parent)
    return final


def stored_path(cfg: PoolConfig, relative_path: str) -> pathlib.Path:
    """返回相对路径在公开存储根中解析出的路径，不检查文件是否存在。"""
    return resolve_safe(cfg.storage_root, relative_path)


def remove_file(cfg: PoolConfig, relative_path: str, *, before_unlink: Callable[[], None] | None = None) -> pathlib.Path:
    """删除白名单资产对应的物理文件。

    目标必须通过安全解析；随后在解析结果上检查符号链接、存在性与目录状态。
    """
    if cfg.guard_system_disk:
        validate_relative_path(relative_path)
        target = cfg.storage_root / relative_path
        try:
            device = _directory_device(cfg.storage_root)
            if device == _directory_device(_system_root()):
                raise SystemDiskGuardError("删除目标位于系统盘（guard_system_disk）")
            with directory(cfg.storage_root, device) as root:
                with directory(target.parent, device) as parent:
                    info = parent.stat(target.name)
                    if not stat.S_ISREG(info.st_mode) or info.st_dev != device or info.st_nlink != 1:
                        raise OSError(errno.EINVAL, "unsafe removal target")
                    root.verify_visible()
                    if before_unlink is not None:
                        before_unlink()
                    try:
                        parent.unlink(target.name, info)
                    except PermissionError:
                        if parent.fd is not None:
                            raise
                        parent.chmod_windows(target.name, info, 0o644)
                        parent.unlink(target.name, info)
                    try:
                        parent.verify_visible()
                        parent.sync()
                    except OSError as exc:
                        raise FilesystemError("删除已执行，但状态核验", exc) from exc
            return target
        except OSError as exc:
            raise FilesystemError("删除文件", exc) from exc

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


_AUDIT_THREAD_LOCK = threading.Lock()


@contextmanager
def _audit_file_lock(path: pathlib.Path):
    """Serialize audit appends across threads and processes.

    One byte of a sidecar lock file is held for the duration of a single
    append. Order is always thread lock -> OS lock, so there is no inversion.
    """
    lock_path = path.with_name(path.name + ".lock")
    with _AUDIT_THREAD_LOCK:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                deadline = time.monotonic() + 5
                while True:
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.005)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_audit_line(path: pathlib.Path, line: bytes) -> None:
    with _audit_file_lock(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        try:
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def audit_writer(cfg: PoolConfig, *, preflight: bool = False):
    if not cfg.guard_system_disk:
        yield (lambda event: _append_record(cfg, event)), (lambda: None)
        return
    path = cfg.records_file.absolute()
    try:
        device = _directory_device(path.parent)
        if device == _directory_device(_system_root()):
            raise SystemDiskGuardError("审计目录与系统盘同设备（guard_system_disk）")
        with ExitStack() as resources:
            try:
                parent = resources.enter_context(directory(path.parent, device, create=not preflight, mode=0o750))
                flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_NONBLOCK", 0)
                fd = parent.open(path.name, flags | (0 if preflight else os.O_CREAT))
            except FileNotFoundError:
                if not preflight:
                    raise
                yield None
                return
            stream = resources.enter_context(os.fdopen(fd, "ab", buffering=0))
            info = _check_file_info(os.fstat(stream.fileno()), device)

            def verify():
                parent.verify_visible()
                if identity(parent.stat(path.name)) != identity(info):
                    raise OSError(errno.ESTALE, "audit file changed")

            def append(event):
                verify()
                line = _record_line(event).encode("utf-8")
                with _audit_file_lock(path):
                    stream.write(line)
                    os.fsync(stream.fileno())
                verify()

            verify()
            yield None if preflight else (append, verify)
    except OSError as exc:
        raise FilesystemError("审计文件操作", exc) from exc


def record(cfg: PoolConfig, event: dict) -> None:
    with audit_writer(cfg) as (append, _verify):
        append(event)


def _record_line(event: dict) -> str:
    event = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
    return json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"


def _append_record(cfg: PoolConfig, event: dict) -> None:
    cfg.records_file.parent.mkdir(parents=True, exist_ok=True)
    _write_audit_line(cfg.records_file, _record_line(event).encode("utf-8"))


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
