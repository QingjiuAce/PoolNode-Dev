"""使用设备替身和真实临时文件覆盖系统盘护栏，不访问网络。"""

from __future__ import annotations

import os
import pathlib

import pytest
import yaml

from conftest import write_config
from helpers import make_png, sha256_of
from pool import cli, storage
from pool.config import ConfigError, load_config
from pool.storage import StorageError, read_records

SYSTEM_DEVICE = 101
SAFE_DEVICE = 202

ASSET_ID = "img-001"
REL_PATH = "img/test.png"


def _device_model(monkeypatch, marker: pathlib.Path, system_prefixes=(),
                  default: int = SAFE_DEVICE):
    """安装设备模型：marker 视为系统盘，指定前缀的目录视为落在系统设备上。

    返回设备探测记录列表（每次 _directory_device 调用追加一个 Path）。
    """
    probes: list[pathlib.Path] = []
    marker_s = os.fspath(marker)
    prefixes = [os.fspath(p) for p in system_prefixes]

    def device(path) -> int:
        probes.append(pathlib.Path(path))
        s = os.fspath(path)
        if s == marker_s or s.startswith(marker_s + os.sep):
            return SYSTEM_DEVICE
        for prefix in prefixes:
            if s == prefix or s.startswith(prefix + os.sep):
                return SYSTEM_DEVICE
        return default

    monkeypatch.setattr(storage, "_system_root", lambda: marker)
    monkeypatch.setattr(storage, "_directory_device", device)
    return probes


def _write_guard_config(tmp_path: pathlib.Path, mode) -> pathlib.Path:
    """生成 guard 配置。mode: 'absent'（删掉键，走缺省）| True | False。"""
    cfg_path = write_config(tmp_path)
    text = cfg_path.read_text(encoding="utf-8")
    if mode == "absent":
        text = "\n".join(
            line for line in text.splitlines()
            if not line.startswith("guard_system_disk")
        )
        cfg_path.write_text(text, encoding="utf-8")
    elif isinstance(mode, bool):
        doc = yaml.safe_load(text)
        doc["guard_system_disk"] = mode
        cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    else:
        raise ValueError(f"unexpected guard mode: {mode!r}")
    return cfg_path


def _write_assets(tmp_path: pathlib.Path, png_sha: str) -> None:
    (tmp_path / "assets.yaml").write_text(yaml.safe_dump({
        "assets": [{
            "asset_id": ASSET_ID,
            "source_url": "http://127.0.0.1:8000/img/test.png",
            "relative_path": REL_PATH,
            "permission_status": "approved",
            "permission_evidence": "确定性测试资产",
            "attribution": "test",
            "license": "test",
            "expected_sha256": png_sha,
            "revision": 1,
        }],
    }), encoding="utf-8")


def _run_sync(tmp_path, monkeypatch, mode, png_bytes, system_prefixes=(),
              default=SAFE_DEVICE, corrupt=False):
    """跑一次完整 CLI sync；返回 (exit_code, config_path, download_calls)。"""
    cfg_path = _write_guard_config(tmp_path, mode)
    _write_assets(tmp_path, sha256_of(png_bytes))
    calls: list[pathlib.Path] = []

    def stub_download(url, cfg, dest, max_size):
        calls.append(dest)
        body = b"corrupt" if corrupt else png_bytes
        dest.write_bytes(body)
        return dest, "image/png", len(body)

    monkeypatch.setattr(cli, "download_to_file", stub_download)
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    _device_model(monkeypatch, tmp_path / "system-root-marker",
                  system_prefixes, default)
    code = cli.main(["--config", str(cfg_path), "sync", ASSET_ID])
    return code, cfg_path, calls


# ---------------------------------------------------------------- 配置解析

def test_guard_defaults_true_when_key_absent(tmp_path):
    assert load_config(_write_guard_config(tmp_path, "absent")).guard_system_disk is True


def test_guard_explicit_values_are_respected(tmp_path):
    assert load_config(_write_guard_config(tmp_path, True)).guard_system_disk is True
    assert load_config(_write_guard_config(tmp_path, False)).guard_system_disk is False


@pytest.mark.parametrize("bad", [None, "false", 1, ["true"], {"x": 1}])
def test_guard_rejects_non_boolean_values(tmp_path, bad):
    cfg_path = write_config(tmp_path)
    doc = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    doc["guard_system_disk"] = bad
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg_path)


# ---------------------------------------------------------------- 路径解析原语

def test_existing_directory_returns_nearest_existing_ancestor(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    assert storage._existing_directory(deep) == tmp_path
    (tmp_path / "a").mkdir()
    assert storage._existing_directory(deep) == tmp_path / "a"
    assert storage._existing_directory(tmp_path) == tmp_path


def test_directory_device_matches_nearest_existing_ancestor(tmp_path):
    deep = tmp_path / "a" / "b"
    assert storage._directory_device(deep) == os.stat(tmp_path).st_dev
    (tmp_path / "a").mkdir()
    assert storage._directory_device(deep) == os.stat(tmp_path / "a").st_dev
    assert storage._directory_device(tmp_path) == storage._directory_device(tmp_path / "a")


# ---------------------------------------------------------------- check_system_disk 契约

def test_check_system_disk_passes_on_independent_devices(tmp_path, monkeypatch):
    cfg = load_config(_write_guard_config(tmp_path, True))
    target = tmp_path / "data" / "img" / "test.png"
    _device_model(monkeypatch, tmp_path / "system-root-marker")
    storage.check_system_disk(cfg, target)  # 不抛


def test_check_system_disk_rejects_target_parent_on_system_device(tmp_path, monkeypatch):
    cfg = load_config(_write_guard_config(tmp_path, True))
    (tmp_path / "data" / "img").mkdir(parents=True)
    target = tmp_path / "data" / "img" / "test.png"
    _device_model(monkeypatch, tmp_path / "system-root-marker",
                  system_prefixes=(str(tmp_path / "data" / "img"),))
    with pytest.raises(StorageError):
        storage.check_system_disk(cfg, target)


# ---------------------------------------------------------------- 完整 CLI sync

def test_sync_succeeds_on_independent_device(tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    code, cfg_path, calls = _run_sync(tmp_path, monkeypatch, True, png)
    assert code == 0
    published = tmp_path / "data" / REL_PATH
    assert published.read_bytes() == png
    assert len(calls) == 1
    assert not calls[0].exists()  # 暂存文件已随发布移走
    events = read_records(load_config(cfg_path))
    assert events[-1]["result"] == "published"
    assert events[-1]["sha256"] == sha256_of(png)


def test_sync_rejects_when_storage_on_system_device(tmp_path, monkeypatch, capsys):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    code, _, calls = _run_sync(tmp_path, monkeypatch, True, png,
                               system_prefixes=(tmp_path,))
    assert code == 1
    assert "guard_system_disk" in capsys.readouterr().err
    assert calls == []                                # 下载前拒绝
    assert not (tmp_path / "data").exists()           # 公开目录未创建
    assert not (tmp_path / "tmp").exists()            # 暂存目录未创建
    # 护栏失败绝不写审计，即使审计目录本身安全
    assert not (tmp_path / "logs" / "sync-records.jsonl").exists()


def test_sync_rejects_when_staging_dir_on_system_device(tmp_path, monkeypatch):
    # 数据盘独立、暂存目录掉回系统盘：仍拒绝（下载前）。
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    (tmp_path / "tmp").mkdir()
    code, _, calls = _run_sync(tmp_path, monkeypatch, True, png,
                               system_prefixes=(str(tmp_path / "tmp"),))
    assert code == 1
    assert calls == []
    assert not (tmp_path / "data").exists()


def test_sync_rejects_when_target_parent_on_system_device(tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    (tmp_path / "data" / "img").mkdir(parents=True)
    code, _, calls = _run_sync(tmp_path, monkeypatch, True, png,
                               system_prefixes=(str(tmp_path / "data" / "img"),))
    assert code == 1
    assert calls == []


def test_sync_rejects_existing_empty_unmounted_data_dir(tmp_path, monkeypatch, capsys):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    data = tmp_path / "data"
    data.mkdir()
    code, _, calls = _run_sync(tmp_path, monkeypatch, "absent", png, system_prefixes=(data,))
    assert code == 1
    assert "storage_root 与系统盘同设备" in capsys.readouterr().err
    assert not calls
    assert list(data.iterdir()) == []
    assert not (tmp_path / "tmp").exists()
    assert not (tmp_path / "logs").exists()


def test_sync_guard_default_true_rejects_without_key(tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    code, _, calls = _run_sync(tmp_path, monkeypatch, "absent", png,
                               system_prefixes=(tmp_path,))
    assert code == 1
    assert calls == []


def test_sync_guard_disabled_ignores_device_layout(tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    code, _, calls = _run_sync(tmp_path, monkeypatch, False, png,
                               system_prefixes=(tmp_path,))
    assert code == 0
    assert (tmp_path / "data" / REL_PATH).read_bytes() == png
    assert len(calls) == 1


def test_sync_guard_disabled_never_probes_devices(tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    cfg_path = _write_guard_config(tmp_path, False)
    _write_assets(tmp_path, sha256_of(png))

    def stub(url, cfg, dest, max_size):
        dest.write_bytes(png)
        return dest, "image/png", len(png)

    def boom(path):
        raise AssertionError("guard 关闭时不应探测设备")

    monkeypatch.setattr(cli, "download_to_file", stub)
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    monkeypatch.setattr(storage, "_directory_device", boom)
    monkeypatch.setattr(storage, "_system_root", lambda: tmp_path / "sys")
    assert cli.main(["--config", str(cfg_path), "sync", ASSET_ID]) == 0
    assert (tmp_path / "data" / REL_PATH).read_bytes() == png


def test_sync_device_probe_error_fails_closed(tmp_path, monkeypatch):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    cfg_path = _write_guard_config(tmp_path, True)
    _write_assets(tmp_path, sha256_of(png))
    calls: list[pathlib.Path] = []

    def stub(url, cfg, dest, max_size):
        calls.append(dest)
        dest.write_bytes(png)
        return dest, "image/png", len(png)

    def boom(path):
        raise OSError("stat failed")

    monkeypatch.setattr(cli, "download_to_file", stub)
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    monkeypatch.setattr(storage, "_directory_device", boom)
    monkeypatch.setattr(storage, "_system_root", lambda: tmp_path / "sys")
    assert cli.main(["--config", str(cfg_path), "sync", ASSET_ID]) == 1
    assert calls == []                                   # 探测失败 → 关闭（拒绝）
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs" / "sync-records.jsonl").exists()


@pytest.mark.parametrize("free_bytes", [None, 0], ids=["disk-usage-error", "low-space"])
def test_sync_refuses_before_writes_when_space_check_fails(tmp_path, monkeypatch, capsys, free_bytes):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)

    def disk_usage(path):
        if free_bytes is None:
            raise OSError("disk usage unavailable")
        return type("Usage", (), {"free": free_bytes})()

    monkeypatch.setattr(cli.shutil, "disk_usage", disk_usage)
    code, _, calls = _run_sync(tmp_path, monkeypatch, True, png)
    assert code == 1
    assert "余量" in capsys.readouterr().err
    assert not calls
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "tmp").exists()
    assert not (tmp_path / "logs").exists()


def test_sync_creates_missing_directories_on_success(tmp_path, monkeypatch):
    # storage_root 与父目录均不存在：护栏经最近存在祖先判定，成功后自动建目录。
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    cfg_path = _write_guard_config(tmp_path, True)
    doc = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    doc["storage_root"] = str(tmp_path / "missing" / "data")
    cfg_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    _write_assets(tmp_path, sha256_of(png))

    def stub(url, cfg, dest, max_size):
        dest.write_bytes(png)
        return dest, "image/png", len(png)

    monkeypatch.setattr(cli, "download_to_file", stub)
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    _device_model(monkeypatch, tmp_path / "system-root-marker")
    assert cli.main(["--config", str(cfg_path), "sync", ASSET_ID]) == 0
    assert (tmp_path / "missing" / "data" / REL_PATH).read_bytes() == png


def test_sync_rejects_when_audit_dir_on_system_device(tmp_path, monkeypatch, capsys):
    # 审计落盘目标（records_file 父目录）在预检范围内：审计目录落在系统设备
    # 时，预检即拒绝 —— 不下载、不写 JSONL。
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    (tmp_path / "logs").mkdir()
    code, _, calls = _run_sync(tmp_path, monkeypatch, True, png,
                               system_prefixes=(str(tmp_path / "logs"),))
    assert code == 1
    assert "guard_system_disk" in capsys.readouterr().err
    assert calls == []                                # 预检拒绝，未下载
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs" / "sync-records.jsonl").exists()


def test_ordinary_rejection_still_audits_when_all_destinations_safe(
        tmp_path, monkeypatch):
    # 与护栏失败相对：所有落盘目标安全时，普通拒绝（格式错误）照常写审计。
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    code, cfg_path, calls = _run_sync(tmp_path, monkeypatch, True, png, corrupt=True)
    assert code == 1
    assert len(calls) == 1                            # 下载已发生（普通拒绝）
    assert not (tmp_path / "data" / REL_PATH).exists()
    events = read_records(load_config(cfg_path))
    assert events[-1]["result"] == "rejected"


def test_sync_refuses_publication_if_device_changes_after_download(tmp_path, monkeypatch, capsys):
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    cfg_path = _write_guard_config(tmp_path, True)
    _write_assets(tmp_path, sha256_of(png))
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    marker = tmp_path / "system-root-marker"
    _device_model(monkeypatch, marker)
    stages = []

    def stub_download(url, cfg, dest, max_size):
        stages.append(dest)
        dest.write_bytes(png)
        _device_model(monkeypatch, marker, system_prefixes=(tmp_path / "data",))
        return dest, "image/png", len(png)

    monkeypatch.setattr(cli, "download_to_file", stub_download)
    assert cli.main(["--config", str(cfg_path), "sync", ASSET_ID]) == 1
    assert "guard_system_disk" in capsys.readouterr().err
    assert len(stages) == 1 and not stages[0].exists()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "logs" / "sync-records.jsonl").exists()


# ---------------------------------------------------------------- publish 复查

def test_publish_rechecks_guard_before_replace(tmp_path, monkeypatch):
    cfg = load_config(_write_guard_config(tmp_path, True))
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    staging = tmp_path / "staging.bin"
    staging.write_bytes(png)
    _device_model(monkeypatch, tmp_path / "system-root-marker",
                  system_prefixes=(tmp_path,))
    with pytest.raises(StorageError):
        storage.publish(cfg, staging, REL_PATH)
    assert staging.exists()                               # 暂存未被消耗
    assert not (tmp_path / "data" / "img" / "test.png").exists()


def test_publish_succeeds_on_independent_device(tmp_path, monkeypatch):
    cfg = load_config(_write_guard_config(tmp_path, True))
    png = make_png(tmp_path / "src.png", 8, 8, seed=7)
    staging = tmp_path / "staging.bin"
    staging.write_bytes(png)
    _device_model(monkeypatch, tmp_path / "system-root-marker")
    final = storage.publish(cfg, staging, REL_PATH)
    assert final.read_bytes() == png
    assert not staging.exists()
