"""Fixture definitions must agree across probe, benchmark, and manifest.

peak_probe.py is deployed standalone on the CN2 box, so it cannot import the
pool package or a shared module at runtime. This test is the single-source
guard: any drift between the three copies fails CI.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve the defining module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


peak = _load('peak_probe_fixture_check', 'peak_probe.py')
bench = _load('bench_fixture_check', 'bench_v05_pairs.py')
MANIFEST = {a['relative_path']: a for a in
            yaml.safe_load((REPO / 'config' / 'bench-assets.yaml')
                           .read_text(encoding='utf-8'))['assets']}

PAIRS = [
    ('bench-6m', 'bench-6m'),
    ('bench-2m', 'bench-2m'),
    ('smoke', 'pool-public-smoke-test'),
]


def test_probe_and_bench_fixtures_agree():
    for peak_name, bench_name in PAIRS:
        path, size, digest = peak.FIXTURES[peak_name]
        entry = bench.FILES[bench_name]
        assert path.lstrip('/') == entry['path']
        assert size == entry['size']
        assert digest == entry['sha']


def test_manifest_matches_code_fixtures():
    for peak_name, bench_name in PAIRS:
        path, size, digest = peak.FIXTURES[peak_name]
        entry = MANIFEST[path.lstrip('/')]
        assert entry['size_bytes'] == size
        assert entry['expected_sha256'] == digest
        assert entry['permission_status'] == 'approved'
