"""Concurrent audit appends: every line must remain complete valid JSON."""
from __future__ import annotations

import json
import threading

from conftest import write_config
from pool import storage
from pool.config import load_config


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
