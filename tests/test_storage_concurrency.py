"""Concurrent audit appends: every line must remain complete valid JSON."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import threading

from conftest import write_config
from pool import storage
from pool.config import load_config

import test_system_disk_guard as guard_helpers

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_concurrent_record_appends_are_whole_lines(tmp_path):
    cfg = load_config(write_config(tmp_path))
    writers, per_writer = 4, 25

    def writer(i):
        for j in range(per_writer):
            storage.record(cfg, {"action": "sync", "asset_id": f"img-{i}",
                                 "revision": 1, "result": "published", "n": j})

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = cfg.records_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == writers * per_writer
    records = [json.loads(line) for line in lines]  # no interleaved fragments
    assert sum(1 for r in records if r["result"] == "published") == len(records)
    assert {r["asset_id"] for r in records} == {f"img-{i}" for i in range(writers)}


def test_guarded_audit_append_concurrent(tmp_path, monkeypatch):
    """Production path: guard_system_disk=True, per-record guarded directory."""
    cfg = load_config(guard_helpers._write_guard_config(tmp_path, True))
    guard_helpers._device_model(monkeypatch, tmp_path / "system-root-marker")
    writers, per_writer = 4, 15

    def writer(i):
        for j in range(per_writer):
            storage.record(cfg, {"action": "sync", "asset_id": f"img-{i}", "n": j})

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = cfg.records_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == writers * per_writer
    [json.loads(line) for line in lines]


def test_cross_process_audit_appends_are_whole_lines(tmp_path):
    cfg_path = write_config(tmp_path)
    code = (
        "import sys\n"
        "from pool.config import load_config\n"
        "from pool import storage\n"
        "cfg = load_config(sys.argv[1])\n"
        "for i in range(30):\n"
        "    storage.record(cfg, {'action': 'sync', 'asset_id': 'p', 'n': i})\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", code, str(cfg_path)],
                              cwd=REPO, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) for _ in range(2)]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    lines = cfg_path.parent.joinpath("logs", "sync-records.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 60
    records = [json.loads(line) for line in lines]
    assert sorted(r["n"] for r in records if r["asset_id"] == "p") == sorted(
        list(range(30)) * 2)
