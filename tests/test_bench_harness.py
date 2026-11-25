"""RC harness policy contract: B is static, D is auto, and the recorded
scheduler (not the label) decides whether an arm is valid."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))

import bench_v05_pairs as bench  # noqa: E402
from pool import fetch  # noqa: E402

BIG = bench.FILES['bench-6m']['size']      # 5403720 >= 4 MiB
MID = bench.FILES['bench-2m']['size']      # 2112525
SMALL = bench.FILES['pool-public-smoke-test']['size']  # 1778514


def _cmd(arm, asset='bench-6m', workers=16, run='exp.001'):
    return bench.build_client_cmd(arm, asset, workers, pathlib.Path('out'),
                                  'assets.yaml', run)[0]


def test_arm_b_passes_static():
    cmd = _cmd('B')
    i = cmd.index('--scheduler')
    assert cmd[i + 1] == 'static'
    assert '--json' in cmd


def test_arm_d_passes_auto():
    cmd = _cmd('D')
    i = cmd.index('--scheduler')
    assert cmd[i + 1] == 'auto'


def test_arm_a_has_no_scheduler_flag_and_one_worker():
    cmd = _cmd('A')
    assert '--scheduler' not in cmd
    i = cmd.index('--workers')
    assert cmd[i + 1] == '1'


def test_metrics_paths_are_unique_per_run():
    cmd1, path1, _ = bench.build_client_cmd('B', 'bench-6m', 16, pathlib.Path('out'),
                                            'assets.yaml', 'exp.001')
    cmd2, path2, _ = bench.build_client_cmd('B', 'bench-6m', 16, pathlib.Path('out'),
                                            'assets.yaml', 'exp.002')
    assert path1 != path2
    assert path1.name == 'exp.001.jsonl' and path2.name == 'exp.002.jsonl'
    assert str(path1) in cmd1 and str(path2) in cmd2


def test_expected_scheduler_mapping():
    assert bench.expected_scheduler('B', BIG, 'auto') == 'static'
    assert bench.expected_scheduler('D', BIG, 'auto') == 'dynamic'
    assert bench.expected_scheduler('D', MID, 'auto') == 'static'
    assert bench.expected_scheduler('D', SMALL, 'auto') == 'static'
    assert bench.expected_scheduler('D', SMALL, 'dynamic') == 'dynamic'
    assert bench.expected_scheduler('A', BIG, 'auto') is None


def test_policy_rejects_b_arm_that_ran_dynamic():
    """The historical defect: label B while the client resolved auto->dynamic."""
    assert bench.policy_verdict('B', BIG, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='dynamic') is False
    assert bench.policy_verdict('B', BIG, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='static') is True


def test_policy_d_requires_auto_resolution():
    assert bench.policy_verdict('D', BIG, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='dynamic') is True
    assert bench.policy_verdict('D', BIG, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='static') is False
    assert bench.policy_verdict('D', MID, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='static') is True
    assert bench.policy_verdict('D', MID, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='dynamic') is False


def test_policy_a_requires_single_stream():
    assert bench.policy_verdict('A', BIG, 'auto', rc=0, sha_ok=True,
                                mode='single', actual='dynamic') is True
    assert bench.policy_verdict('A', BIG, 'auto', rc=0, sha_ok=True,
                                mode='range', actual='static') is False


@pytest.mark.parametrize('size,expected', [(BIG, 'dynamic'), (MID, 'static'),
                                          (SMALL, 'static')])
def test_fetch_auto_resolution_is_recorded(monkeypatch, tmp_path, size, expected):
    """Actual client output (metrics download record), not a harness label."""
    monkeypatch.setattr(fetch, 'select',
                        lambda *a: ('https://dl.bszx.site/x.jpg', 'a' * 64, size))
    monkeypatch.setattr(fetch, 'download',
                        lambda *a: 'range')
    path = tmp_path / 'm.jsonl'
    rc = fetch.main(['asset', str(tmp_path / 'out.jpg'), '--assets', 'any.yaml',
                     '--metrics', str(path), '--json'])
    assert rc == 0
    rows = [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
    assert rows[-1]['kind'] == 'download'
    assert rows[-1]['scheduler'] == expected


def test_harness_skips_instead_of_interleaving(tmp_path, capsys):
    (tmp_path / 'runs.jsonl.lock').write_text('12345', encoding='utf-8')
    rc = bench.main(['--out', str(tmp_path), '--arms', 'B,D', '--pairs', '1'])
    assert rc == 0
    assert not (tmp_path / 'runs.jsonl').exists()
    assert 'SKIPPED_OVERLAP' in capsys.readouterr().out


def test_manifest_is_append_only(tmp_path):
    path = tmp_path / 'runs.jsonl'
    bench.append_jsonl(path, {'run_id': 'r1', 'arm': 'B'})
    bench.append_jsonl(path, {'run_id': 'r2', 'arm': 'D'})
    rows = [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
    assert [r['run_id'] for r in rows] == ['r1', 'r2']


def test_missing_assets_manifest_fails_before_network(tmp_path, capsys):
    rc = bench.main(['--out', str(tmp_path), '--pairs', '1', '--arms', 'B,D',
                     '--assets', str(tmp_path / 'missing.yaml')])
    assert rc == 2
    assert 'assets manifest not found' in capsys.readouterr().err
    assert not (tmp_path / 'runs.jsonl').exists()


def test_default_assets_manifest_exists():
    assert bench.ASSETS.is_file()
    assert bench.ASSETS.name == 'bench-assets.yaml'


def _fake_client(monkeypatch, out, scheduler, *, mode='range', run_id_override=None):
    def fake_run(cmd, **kwargs):
        metrics_path = pathlib.Path(cmd[cmd.index('--metrics') + 1])
        run_id = cmd[cmd.index('--run-id') + 1]
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        metrics_path.write_text(json.dumps({
            'kind': 'download', 'scheduler': scheduler, 'mode': mode,
            'run_id': run_id_override or run_id}) + '\n', encoding='utf-8')
        (out / 'dl.bin').write_bytes(b'x')
        stdout = json.dumps({'scheduler': scheduler, 'mode': mode})
        return subprocess.CompletedProcess(cmd, 0, stdout, '')

    monkeypatch.setattr(bench.subprocess, 'run', fake_run)
    monkeypatch.setattr(bench, 'sha256_file',
                        lambda path: bench.FILES['bench-6m']['sha'])


def test_run_client_verifies_actual_metrics_for_arm_b(monkeypatch, tmp_path):
    out = tmp_path / 'out'
    (out / 'metrics').mkdir(parents=True)
    _fake_client(monkeypatch, out, 'static')
    rec = bench.run_client('B', 16, 'bench-6m', out, 'assets.yaml', 'run.001')
    assert rec['policy_ok'] is True
    assert rec['metrics_ok'] is True
    assert rec['actual_scheduler'] == 'static'
    assert rec['scheduler_arg'] == 'static'


def test_run_client_rejects_b_that_ran_dynamic(monkeypatch, tmp_path):
    out = tmp_path / 'out'
    (out / 'metrics').mkdir(parents=True)
    _fake_client(monkeypatch, out, 'dynamic')
    rec = bench.run_client('B', 16, 'bench-6m', out, 'assets.yaml', 'run.001')
    assert rec['policy_ok'] is False
    assert rec['actual_scheduler'] == 'dynamic'


def test_run_client_rejects_metrics_run_id_mismatch(monkeypatch, tmp_path):
    out = tmp_path / 'out'
    (out / 'metrics').mkdir(parents=True)
    _fake_client(monkeypatch, out, 'static', run_id_override='other-run')
    rec = bench.run_client('B', 16, 'bench-6m', out, 'assets.yaml', 'run.001')
    assert rec['metrics_ok'] is False
    assert rec['policy_ok'] is False


def test_run_client_records_overridden_d_scheduler_arg(monkeypatch, tmp_path):
    out = tmp_path / 'out'
    (out / 'metrics').mkdir(parents=True)
    _fake_client(monkeypatch, out, 'dynamic')
    rec = bench.run_client('D', 16, 'bench-6m', out, 'assets.yaml', 'run.001',
                           d_cli='dynamic')
    assert rec['scheduler_arg'] == 'dynamic'
    assert rec['policy_ok'] is True
