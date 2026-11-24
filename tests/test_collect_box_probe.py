"""Offline checks for scripts/collect_box_probe.py (no network)."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'collect_box_probe.py'
spec = importlib.util.spec_from_file_location('collect_box_probe', SCRIPT)
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


def rec(ts):
    return json.dumps({'ts': ts, 'tag': 'cn2box', 'arms': []}, ensure_ascii=False).encode() + b'\n'


def test_merge_append_only_new_tail(tmp_path):
    archive = tmp_path / 'cn2.jsonl'
    first = rec('a')
    archive.write_bytes(first)
    added = cb.merge_append(archive, first + rec('b'))
    assert added == 1
    assert archive.read_bytes() == first + rec('b')


def test_merge_append_requires_prefix(tmp_path):
    archive = tmp_path / 'cn2.jsonl'
    archive.write_bytes(rec('a'))
    with pytest.raises(RuntimeError, match='diverges'):
        cb.merge_append(archive, rec('x') + rec('b'))


def test_merge_append_rejects_broken_jsonl(tmp_path):
    archive = tmp_path / 'cn2.jsonl'
    archive.write_bytes(b'')
    with pytest.raises(ValueError):
        cb.merge_append(archive, b'not json\n')
    assert archive.read_bytes() == b''  # nothing written on invalid payload


def test_validate_jsonl_requires_ts_and_arms():
    assert cb.validate_jsonl(rec('a') + rec('b')) == 2
    with pytest.raises(ValueError, match='missing ts/arms'):
        cb.validate_jsonl(b'{"ts": "a"}\n')
