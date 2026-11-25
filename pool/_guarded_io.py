"""Anchor writes to checked directories on POSIX; Windows uses identity checks."""
from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path


POSIX = os.name != "nt"


def identity(info):
    return info.st_dev, info.st_ino


def _plain(info):
    return not (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400)


def _check_name(name):
    if not name or name in (".", "..") or Path(name).name != name or Path(name).anchor:
        raise OSError(errno.EINVAL, "invalid child name")


class GuardedDirectory:
    def __init__(self, path, fd, info):
        self.path, self.fd, self.info = path, fd, info

    def verify_visible(self):
        current = self.path.lstat()
        if not _plain(current) or identity(current) != identity(self.info):
            raise OSError(errno.ESTALE, "directory changed")

    def _child(self, name):
        _check_name(name)
        return (name, {"dir_fd": self.fd}) if self.fd is not None else (self.path / name, {})

    def stat(self, name):
        path, kw = self._child(name)
        info = os.stat(path, follow_symlinks=False, **kw)
        if not _plain(info):
            raise OSError(errno.ELOOP, "unsafe child link")
        return info

    def open(self, name, flags, mode=0o600):
        self.verify_visible()
        path, kw = self._child(name)
        if POSIX:
            flags |= os.O_NOFOLLOW
        else:
            try:
                self.stat(name)
            except FileNotFoundError:
                pass
        return os.open(path, flags | getattr(os, "O_BINARY", 0), mode, **kw)

    def unlink(self, name, info, *, cleanup=False):
        if not cleanup or self.fd is None:
            self.verify_visible()
        if identity(self.stat(name)) != identity(info):
            raise OSError(errno.ESTALE, "file changed")
        path, kw = self._child(name)
        os.unlink(path, **kw)

    def replace_to(self, name, target, final_name):
        self.verify_visible()
        target.verify_visible()
        source, _ = self._child(name)
        dest, _ = target._child(final_name)
        if POSIX:
            os.replace(source, dest, src_dir_fd=self.fd, dst_dir_fd=target.fd)
        else:
            os.replace(source, dest)

    def chmod_windows(self, name, info, mode):
        self.verify_visible()
        if identity(self.stat(name)) != identity(info):
            raise OSError(errno.ESTALE, "file changed before chmod")
        os.chmod(self.path / name, mode)

    def sync(self):
        if self.fd is not None:
            os.fsync(self.fd)


def _open_directory(name, flags, parent=None):
    fd = os.open(name, flags, dir_fd=parent)
    try:
        return fd, os.fstat(fd)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def directory(path: Path, device: int, *, create=False, mode=0o755):
    """Open without traversing links; create only below a verified device."""
    path = path.absolute()
    if ".." in path.parts:
        raise OSError(errno.EINVAL, "ambiguous directory path")
    current = Path(path.anchor)
    fd = None
    try:
        if POSIX:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(current, flags)
        info = os.fstat(fd) if POSIX else current.lstat()
        for name in path.parts[1:]:
            parent = GuardedDirectory(current, fd, info)
            current = current / name
            try:
                next_fd, next_info = (_open_directory(name, flags, fd) if POSIX
                                      else (None, current.lstat()))
            except FileNotFoundError:
                if not create:
                    raise
                if info.st_dev != device:
                    raise OSError(errno.EXDEV, "unsafe directory creation device")
                parent.verify_visible()
                try:
                    if POSIX:
                        os.mkdir(name, mode, dir_fd=fd)
                    else:
                        current.mkdir(mode=mode)
                except FileExistsError:
                    pass  # Another writer may have created it; still open and verify it.
                next_fd, next_info = (_open_directory(name, flags, fd) if POSIX
                                      else (None, current.lstat()))
                if next_info.st_dev != device:
                    if next_fd is not None:
                        os.close(next_fd)
                    raise OSError(errno.EXDEV, "created directory changed device")
            if fd is not None:
                os.close(fd)
            fd, info = next_fd, next_info
            if not _plain(info) or not stat.S_ISDIR(info.st_mode):
                raise OSError(errno.ENOTDIR, "unsafe directory component")
        if info.st_dev != device:
            raise OSError(errno.EXDEV, "unexpected directory device")
        handle = GuardedDirectory(path, fd, info)
        handle.verify_visible()
        yield handle
    finally:
        if fd is not None:
            os.close(fd)
