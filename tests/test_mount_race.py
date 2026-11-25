"""Defensive filesystem failures using synthetic assets and real temporary files."""
import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import make_png, sha256_of
from pool import cli, storage
from pool.config import load_config
from test_system_disk_guard import (
    ASSET_ID, REL_PATH, _device_model, _write_assets, _write_guard_config,
)


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    config = _write_guard_config(tmp_path, True)
    cfg = load_config(config)
    png = make_png(tmp_path / "source.png", 8, 8, seed=4)
    _write_assets(tmp_path, sha256_of(png))
    _device_model(monkeypatch, tmp_path / "system")
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    calls = []

    def download(url, upstream, stream, max_size):
        assert os.fstat(stream.fileno()).st_dev == tmp_path.stat().st_dev
        assert not isinstance(stream, Path)
        calls.append(stream)
        stream.write(png)
        return stream, "image/png", len(png)

    monkeypatch.setattr(cli, "download_to_file", download)
    def run(command="sync"):
        return cli.main(["--config", str(config), command, ASSET_ID])

    return cfg, cfg.storage_root / REL_PATH, calls, run


@pytest.mark.parametrize("code", [errno.EXDEV, errno.ENOSPC, errno.EACCES, errno.ENOENT, errno.EIO])
def test_replace_errors_keep_old_bytes_and_clean_stage(guarded, monkeypatch, capsys, code):
    cfg, target, calls, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old bytes")

    def fail(*args):
        raise OSError(code, "private details", str(target))

    monkeypatch.setattr(storage.GuardedDirectory, "replace_to", fail)
    assert run() == 1
    output = capsys.readouterr().err
    assert errno.errorcode[code] in output and "private details" not in output
    assert len(calls) == 1
    assert target.read_bytes() == b"old bytes"
    assert not list(cfg.tmp_dir.iterdir())
    assert not list(target.parent.glob(".*.staging-*"))
    assert storage.read_records(cfg)[-1]["result"] == "rejected"


def test_staging_object_verification_failure_stops_download_and_cleans_file(guarded, monkeypatch):
    cfg, target, calls, run = guarded
    original = storage._check_file_info

    def reject(info, dev):
        return original(info, dev + 1)

    monkeypatch.setattr(storage, "_check_file_info", reject)
    assert run() == 1
    assert not calls and not target.exists()
    assert not list(cfg.tmp_dir.iterdir())


@pytest.mark.parametrize("command", ["sync", "remove"])
def test_audit_open_denial_prevents_mutation(guarded, monkeypatch, command):
    cfg, target, calls, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    cfg.records_file.parent.mkdir()
    cfg.records_file.touch()
    original = storage.GuardedDirectory.open

    def open_child(self, name, *args):
        if name == cfg.records_file.name:
            raise OSError(errno.EACCES, "audit is read-only")
        return original(self, name, *args)

    monkeypatch.setattr(storage.GuardedDirectory, "open", open_child)
    assert run(command) == 1
    assert target.read_bytes() == b"old" and not calls
    assert not cfg.tmp_dir.exists() and cfg.records_file.read_bytes() == b""


def test_remove_rejects_audit_descriptor_on_unexpected_device(guarded, monkeypatch):
    cfg, target, _, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"keep")
    cfg.records_file.parent.mkdir()
    cfg.records_file.write_bytes(b"")
    original = storage._check_file_info
    audit_inode = cfg.records_file.stat().st_ino

    def verify(info, device):
        return original(info, device + (info.st_ino == audit_inode))

    monkeypatch.setattr(storage, "_check_file_info", verify)
    assert run("remove") == 1
    assert target.read_bytes() == b"keep"
    assert cfg.records_file.read_bytes() == b""
    assert not cfg.tmp_dir.exists()


def test_remove_success_and_audit_without_staging(guarded):
    cfg, target, calls, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    assert run("remove") == 0
    assert not target.exists() and not cfg.tmp_dir.exists() and not calls
    assert storage.read_records(cfg)[-1]["result"] == "removed"


def test_remove_reports_post_delete_audit_failure(guarded, monkeypatch, capsys):
    cfg, target, _, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    original = os.fsync

    def fail_audit(fd):
        if os.fstat(fd).st_ino == cfg.records_file.stat().st_ino:
            raise OSError(errno.EIO, "audit flush failed")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fail_audit)
    assert run("remove") == 1
    assert not target.exists()
    captured = capsys.readouterr()
    assert "文件已删除，但审计未完成" in captured.err
    assert "REMOVED:" not in captured.out


def test_low_space_keeps_original_rejection_if_audit_fails(guarded, monkeypatch, capsys):
    cfg, target, calls, run = guarded
    monkeypatch.setattr(cli.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))

    def fail(*args):
        raise OSError(errno.ENOSPC, "audit full")

    monkeypatch.setattr(storage.GuardedDirectory, "open", fail)
    assert run() == 1
    error = capsys.readouterr().err
    assert "余量" in error and "审计未写入" in error and "ENOSPC" in error
    assert not target.exists() and not calls
    assert not cfg.tmp_dir.exists() and not cfg.records_file.exists()


@pytest.mark.parametrize("mismatch", ["storage_root", "target_parent"])
def test_guarded_cross_device_publication_preserves_existing_target(guarded, monkeypatch, mismatch):
    cfg, target, calls, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old bytes")
    original = storage._directory_device
    different = cfg.storage_root if mismatch == "storage_root" else target.parent

    def device(path):
        value = original(path)
        return value + 1 if path == different else value

    monkeypatch.setattr(storage, "_directory_device", device)
    if mismatch == "target_parent":
        # The final directory's real descriptor must also agree with the expected device.
        original_directory = storage.directory
        monkeypatch.setattr(storage, "directory", lambda path, dev, **kw: original_directory(
            path, device(path) if path == different else dev, **kw
        ))
    assert run() == 1
    assert target.read_bytes() == b"old bytes"
    assert not list(cfg.tmp_dir.glob("*.part"))
    assert not list(target.parent.glob(".*.staging-*"))


def test_opt_out_keeps_cross_device_copy(tmp_path, monkeypatch):
    cfg = load_config(_write_guard_config(tmp_path, False))
    cfg.tmp_dir.mkdir()
    stage = cfg.tmp_dir / "asset.part"
    stage.write_bytes(b"verified bytes")
    real_stat = os.stat

    def device_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        if path == stage:
            return SimpleNamespace(st_dev=info.st_dev + 1)
        return info

    monkeypatch.setattr(os, "stat", device_stat)
    final = storage.publish(cfg, stage, REL_PATH)
    assert final.read_bytes() == b"verified bytes"
    assert not stage.exists()
    assert not list(final.parent.glob(".*.staging-*"))


@pytest.mark.parametrize("command", ["verify", "check-upstream"])
def test_read_only_commands_do_not_open_audit(guarded, monkeypatch, command):
    cfg, target, calls, run = guarded
    target.parent.mkdir(parents=True)
    target.write_bytes((cfg.storage_root.parent / "source.png").read_bytes())

    def unexpected(*args, **kwargs):
        pytest.fail("read-only command opened audit writer")

    monkeypatch.setattr(cli, "audit_writer", unexpected)
    monkeypatch.setattr(cli, "check_upstream", lambda *args: {
        "dns": {"addresses": [], "ms": 0}, "tcp": {"peer": "local", "ms": 0},
        "tls": {"ok": False}, "head": {}, "http": {"content_type": "image/png", "content_length": 1},
        "get": {"status": 200, "ttfb_ms": 0, "sample_bytes": 1, "total_ms": 1,
                "sample_speed_bps": 1, "sample_is_image_magic": True},
    })
    assert run(command) == 0
    assert not cfg.records_file.parent.exists() and not cfg.tmp_dir.exists() and not calls
