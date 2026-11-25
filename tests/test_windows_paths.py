"""Windows path-boundary regressions: upward probes must terminate at a drive
root, and a regular file is never treated as a directory."""
from __future__ import annotations

import dataclasses
import pathlib

import pytest

from conftest import write_config
from pool import cli, storage
from pool.config import load_config


def _config(tmp_path, storage_root):
    cfg = load_config(write_config(tmp_path))
    return dataclasses.replace(cfg, storage_root=pathlib.Path(storage_root))


def test_capacity_probe_stops_at_drive_root(monkeypatch, tmp_path):
    """cli.py used to loop forever when storage_root was a drive root."""
    root = pathlib.Path(tmp_path.anchor)
    cfg = _config(tmp_path, root)
    seen = []
    real = cli.shutil.disk_usage
    monkeypatch.setattr(cli.shutil, 'disk_usage',
                        lambda p: seen.append(p) or real(p))
    cli._check_storage_capacity(cfg)
    assert seen == [root]


def test_capacity_probe_terminates_when_no_ancestor_exists(monkeypatch, tmp_path):
    cfg = _config(tmp_path, pathlib.Path(tmp_path.anchor) / 'pool-missing-xyz')
    calls = []

    def fake_exists(self):
        calls.append(self)
        return False

    monkeypatch.setattr(pathlib.Path, 'exists', fake_exists)
    monkeypatch.setattr(cli.shutil, 'disk_usage',
                        lambda p: type('Usage', (), {'free': 10 ** 12})())
    cli._check_storage_capacity(cfg)
    assert len(calls) < 64
    assert calls[-1] == calls[-1].parent  # stopped at the anchor, not looping


def test_existing_directory_rejects_regular_file(tmp_path):
    path = tmp_path / 'file.bin'
    path.write_bytes(b'x')
    with pytest.raises(NotADirectoryError):
        storage._existing_directory(path)


def test_existing_directory_terminates_at_anchor(monkeypatch, tmp_path):
    missing = pathlib.Path(tmp_path.anchor) / 'pool-missing-xyz'
    real_stat = pathlib.Path.stat

    def fake_stat(self, *args, **kwargs):
        if self == missing or self == missing.parent:
            raise FileNotFoundError
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, 'stat', fake_stat)
    with pytest.raises(FileNotFoundError):
        storage._existing_directory(missing)
