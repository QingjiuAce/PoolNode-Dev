"""Paired A/B/C/D throughput harness: v0.4 static vs v0.5 auto scheduler.

Arm identities are declared before the run and verified afterwards from the
client's own metrics, not inferred from a flag being present or absent:

  A  client single-stream        workers=1,  no --scheduler,  mode=single
  B  client v0.4 static          workers=N,  --scheduler static
  C  bare-curl Range control     same span boundaries the client computes
  D  client v0.5 auto            workers=N,  --scheduler auto
     expected resolution: bench-6m (5.4 MiB) -> dynamic; 1.7/2.0 MiB -> static

Every run gets a unique metrics file (<metrics_run_id>.jsonl) with the run id
stamped into every record; the manifest is append-only and carries the
experiment run id, arm id, the CLI scheduler argument, the scheduler actually
recorded by the client, and a policy_ok verdict. A lock file turns overlapping
invocations into SKIPPED_OVERLAP instead of interleaving network traffic.

Assembly in arm C is by explicit numeric order (the seg_* lex-sort bug is a
known experiment-script pitfall and is avoided here by construction).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pool.origin import DEFAULT_ORIGIN, normalize_origin  # noqa: E402

ASSETS = REPO / 'config' / 'bench-assets.yaml'
AUTO_DYNAMIC_BYTES = 4 * 1024 * 1024  # keep in step with pool.fetch

FILES = {
    'bench-6m': {'path': 'pool-tests/bench-6m.jpg', 'size': 5403720,
                 'sha': '294d65cc0751226ef6691e5645f5625f09d8c24b6389e3f451d467b38f6359ed'},
    'bench-2m': {'path': 'pool-tests/bench-2m.jpg', 'size': 2112525,
                 'sha': 'cb7926ffb9bb13c6a48efffd22412353ea6450d322feb6be1147ee6f204b6530'},
    'pool-public-smoke-test': {
        'path': 'submissions/originals/pool-public-smoke-test.jpg', 'size': 1778514,
        'sha': 'b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e'},
}


@dataclass(frozen=True)
class ArmSpec:
    workers: int | None          # None -> use --workers
    cli_scheduler: str | None    # None -> do not pass --scheduler
    expected_mode: str


ARM_SPECS = {
    'A': ArmSpec(workers=1, cli_scheduler=None, expected_mode='single'),
    'B': ArmSpec(workers=None, cli_scheduler='static', expected_mode='range'),
    'C': ArmSpec(workers=None, cli_scheduler=None, expected_mode='curl-range'),
    'D': ArmSpec(workers=None, cli_scheduler='auto', expected_mode='range'),
}


def expected_scheduler(arm: str, size: int, d_cli: str) -> str | None:
    """The scheduler the client must record for this arm (None = not checked)."""
    if arm == 'B':
        return 'static'
    if arm == 'D':
        if d_cli == 'auto':
            return 'dynamic' if size >= AUTO_DYNAMIC_BYTES else 'static'
        return d_cli
    if arm == 'C':
        return 'curl'
    return None  # A: single GET; scheduler resolution is irrelevant


def client_spans(size: int, workers: int) -> list[tuple[int, int]]:
    """Exactly the v0.4 boundary formula (range_transport._ranges)."""
    n = min(workers, size)
    return [(size * i // n, size * (i + 1) // n - 1) for i in range(n)]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blk in iter(lambda: f.read(1 << 20), b''):
            h.update(blk)
    return h.hexdigest()


def build_client_cmd(arm, asset, workers, out, assets, metrics_run_id, d_cli='auto',
                     origin=DEFAULT_ORIGIN):
    """Return (cmd, metrics_path, spec). The arm's CLI contract lives here."""
    spec = ARM_SPECS[arm]
    metrics_path = out / 'metrics' / f'{metrics_run_id}.jsonl'
    cmd = [sys.executable, '-m', 'pool.fetch', asset, str(out / 'dl.bin'),
           '--assets', str(assets), '--workers', str(spec.workers or workers),
           '--metrics', str(metrics_path), '--run-id', metrics_run_id, '--json',
           '--origin', origin]
    scheduler = spec.cli_scheduler
    if arm == 'D':
        scheduler = d_cli
    if scheduler is not None:
        cmd += ['--scheduler', scheduler]
    return cmd, metrics_path, spec


def read_download_record(metrics_path: Path) -> dict | None:
    try:
        with open(metrics_path, encoding='utf-8') as fh:
            records = [json.loads(line) for line in fh if line.strip()]
    except (OSError, ValueError):
        return None
    downloads = [r for r in records if r.get('kind') == 'download']
    return downloads[-1] if downloads else None


def last_json_line(stdout: str) -> dict | None:
    for line in reversed((stdout or '').splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def policy_verdict(arm: str, size: int, d_cli: str, *, rc: int, sha_ok: bool,
                   mode: str | None, actual: str | None) -> bool:
    """Arm identity is verified from the client's own output."""
    spec = ARM_SPECS[arm]
    expected = expected_scheduler(arm, size, d_cli)
    return (rc == 0 and sha_ok and mode == spec.expected_mode
            and (expected is None or actual == expected))


def run_client(arm, workers, asset, out, assets, metrics_run_id, d_cli='auto',
               origin=DEFAULT_ORIGIN):
    spec = ARM_SPECS[arm]
    size = FILES[asset]['size']
    expected = expected_scheduler(arm, size, d_cli)
    cmd, metrics_path, _ = build_client_cmd(
        arm, asset, workers, out, assets, metrics_run_id, d_cli, origin)
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                       errors='replace', timeout=900)
    wall = time.perf_counter() - t0
    sha_ok = False
    if (out / 'dl.bin').exists():
        sha_ok = sha256_file(out / 'dl.bin') == FILES[asset]['sha']
        (out / 'dl.bin').unlink()
    result = last_json_line(p.stdout)
    metrics_rec = read_download_record(metrics_path)
    actual = (result or {}).get('scheduler')
    mode = (result or {}).get('mode')
    if actual is None and metrics_rec is not None:
        actual = metrics_rec.get('scheduler')
    if mode is None and metrics_rec is not None:
        mode = metrics_rec.get('mode')
    policy_ok = policy_verdict(arm, size, d_cli, rc=p.returncode, sha_ok=sha_ok,
                               mode=mode, actual=actual)
    return {
        'arm': arm, 'workers': spec.workers or workers,
        'wall_ms': round(wall * 1000, 2), 'sha_ok': sha_ok,
        'mode': mode, 'rc': p.returncode,
        'scheduler_arg': ('auto' if arm == 'D' else spec.cli_scheduler),
        'expected_scheduler': expected, 'actual_scheduler': actual,
        'policy_ok': policy_ok, 'metrics_run_id': metrics_run_id,
        'metrics_file': str(metrics_path.relative_to(out)),
        'err': (p.stderr or '')[-300:] if p.returncode else '',
    }


def run_curl_range(arm_id, asset, workers, out, origin=DEFAULT_ORIGIN):
    f = FILES[asset]
    url = f'{origin}/{f["path"]}'
    spans = client_spans(f['size'], workers)
    t0 = time.perf_counter()
    procs = []
    for i, (s, e) in enumerate(spans):
        procs.append(subprocess.Popen(
            ['curl', '-sS', '-r', f'{s}-{e}', '-o', str(out / f'part{i}.bin'),
             '--max-time', '600', '-w', '%{speed_download} %{time_total} %{http_code}',
             url], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
    speeds = [p.communicate()[0].decode(errors='replace').strip() for p in procs]
    rcs = [p.returncode for p in procs]
    wall = time.perf_counter() - t0
    # explicit numeric-order assembly (never shell glob lex order)
    assembled = out / 'asm.bin'
    with open(assembled, 'wb') as dst:
        for i in range(len(spans)):
            dst.write((out / f'part{i}.bin').read_bytes())
    sha_ok = sha256_file(assembled) == f['sha']
    assembled.unlink()
    for i in range(len(spans)):
        (out / f'part{i}.bin').unlink()
    codes = [s.split()[2] if len(s.split()) > 2 else '?' for s in speeds]
    policy_ok = sha_ok and all(rc == 0 for rc in rcs) and '206' in codes
    return {'arm': 'C', 'workers': workers, 'wall_ms': round(wall * 1000, 2),
            'sha_ok': sha_ok, 'mode': 'curl-range', 'rc': rcs,
            'scheduler_arg': None, 'expected_scheduler': 'curl',
            'actual_scheduler': 'curl', 'policy_ok': policy_ok,
            'metrics_run_id': arm_id, 'metrics_file': None,
            'seg_speeds': [s.split()[0] for s in speeds], 'http_codes': codes,
            'err': ''}


def append_jsonl(path: Path, rec: dict) -> None:
    """One write + fsync per record; never rewrite existing lines."""
    line = (json.dumps(rec, ensure_ascii=False) + '\n').encode('utf-8')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def _acquire_lock(lock_path: Path, stale_s: int = 3 * 3600):
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        try:
            if time.time() - lock_path.stat().st_mtime > stale_s:
                lock_path.unlink(missing_ok=True)
                return _acquire_lock(lock_path, stale_s)
        except OSError:
            pass
        return None


def summarize(runs):
    print('\n=== 汇总（按 arm × workers）===')
    groups = {}
    for r in runs:
        if not r.get('sha_ok') or r.get('wall_ms') is None:
            continue
        size = FILES[r['asset']]['size']
        r['mbps'] = round(size * 8 / 1e6 / (r['wall_ms'] / 1000), 2)
        groups.setdefault((r['arm'], r['workers']), []).append(r)
    for (arm, w), rs in sorted(groups.items()):
        m = [r['mbps'] for r in rs]
        wall = sorted(r['wall_ms'] for r in rs)
        p90 = wall[min(len(wall) - 1, max(0, int(round(0.9 * len(wall))) - 1))]
        policy = sum(bool(r.get('policy_ok')) for r in rs)
        print(f'  {arm} w{w}: n={len(rs)} 中位={statistics.median(m):.2f} Mbps '
              f'范围={min(m):.2f}-{max(m):.2f} P90墙钟={p90:.0f}ms '
              f'最差={max(m):.2f} Mbps SHA={sum(r["sha_ok"] for r in rs)}/{len(rs)} '
              f'policy={policy}/{len(rs)}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--asset', default='bench-6m', choices=sorted(FILES))
    ap.add_argument('--assets', default=str(ASSETS),
                    help='trusted whitelist YAML; bench fixtures live in a '
                         'maintainer-side list, not the example file')
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--pairs', type=int, default=5, help='rounds of interleaved arms')
    ap.add_argument('--arms', default='A,B,C,D')
    ap.add_argument('--scheduler', default='auto', choices=('auto', 'static', 'dynamic'),
                    help='arm D CLI scheduler value (default auto: exercise the '
                         'real auto-resolution path)')
    ap.add_argument('--gap', type=float, default=2.0, help='seconds between runs')
    ap.add_argument('--out', default=str(REPO / 'reports' / 'benchmarks' / 'v05-pairs'))
    ap.add_argument('--origin', default=os.environ.get('POOL_ORIGIN', DEFAULT_ORIGIN),
                    help='内容源 origin（默认 POOL_ORIGIN 环境变量或生产 origin）')
    args = ap.parse_args(argv)
    try:
        origin = normalize_origin(args.origin)
    except ValueError as exc:
        print(f'invalid --origin: {exc}', file=sys.stderr)
        return 2
    out = Path(args.out)
    assets_path = Path(args.assets)
    if not assets_path.is_file():
        print(f'assets manifest not found: {assets_path} '
              f'(set --assets; default {ASSETS})', file=sys.stderr)
        return 2
    (out / 'metrics').mkdir(parents=True, exist_ok=True)
    arms = [a.strip().upper() for a in args.arms.split(',') if a.strip()]
    unknown = [a for a in arms if a not in ARM_SPECS]
    if unknown:
        print(f'unknown arm(s): {unknown}', file=sys.stderr)
        return 2
    if args.pairs < 1:
        print('--pairs must be >= 1', file=sys.stderr)
        return 2

    runs_path = out / 'runs.jsonl'
    lock = _acquire_lock(Path(str(runs_path) + '.lock'))
    if lock is None:
        print(f'SKIPPED_OVERLAP: another benchmark holds {runs_path}.lock')
        return 0

    experiment_run_id = (time.strftime('%Y%m%dT%H%M%S') + '-'
                         + secrets.token_hex(3))
    seq = 0
    try:
        for rnd in range(args.pairs):
            order = arms if rnd % 2 == 0 else arms[::-1]
            for arm in order:
                seq += 1
                metrics_run_id = f'{experiment_run_id}.{seq:03d}'
                rec = {'seq': seq, 'round': rnd, 'arm': arm,
                       'arm_id': metrics_run_id, 'run_id': experiment_run_id,
                       'asset': args.asset,
                       'workers': (ARM_SPECS[arm].workers or args.workers)
                       if arm != 'C' else args.workers,
                       'ts': round(time.time(), 3)}
                try:
                    if arm == 'C':
                        rec.update(run_curl_range(metrics_run_id, args.asset,
                                                  args.workers, out, origin))
                    else:
                        rec.update(run_client(arm, args.workers, args.asset, out,
                                              args.assets, metrics_run_id,
                                              args.scheduler, origin))
                except Exception as exc:  # keep the round going; record and continue
                    rec.update({'policy_ok': False,
                                'error': f'{exc.__class__.__name__}: {exc}'[:200]})
                append_jsonl(runs_path, rec)
                print(json.dumps(rec, ensure_ascii=False), flush=True)
                time.sleep(args.gap)
        runs = [json.loads(l) for l in open(runs_path, encoding='utf-8')]
        runs = [r for r in runs if r.get('run_id') == experiment_run_id]
        summarize(runs)
        violations = [r for r in runs if not r.get('policy_ok')]
        if violations:
            print(f'\nPOLICY VIOLATIONS: {len(violations)}/{len(runs)} '
                  f'(see arm/expected/actual fields)')
            return 1
        return 0
    finally:
        os.close(lock)
        Path(str(runs_path) + '.lock').unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
