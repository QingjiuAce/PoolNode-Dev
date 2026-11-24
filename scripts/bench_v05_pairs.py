"""Paired A/B/C/D throughput harness: v0.4 static vs v0.5 dynamic scheduler.

Arms (interleaved within each round, round order alternates; never concurrent
on the wire):
  A  client single-stream (--workers 1, single GET)
  B  client v0.4 static equal-split Range (--workers N)
  C  bare-curl Range control (same span boundaries the client computes)
  D  client v0.5 dynamic chunk queue (--scheduler dynamic, same workers cap)

All arms use the same self-generated registered fixture, same SHA256, same
line. Records append to <out>/runs.jsonl; per-run phase metrics are kept in
<out>/metrics/<seq>.jsonl. Assembly in arm C is by explicit numeric order
(the seg_* lex-sort bug is a known experiment-script pitfall and is avoided
here by construction).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / 'config' / 'assets.example.yaml'

FILES = {
    'bench-6m': {'path': 'pool-tests/bench-6m.jpg', 'size': 5403720,
                 'sha': '294d65cc0751226ef6691e5645f5625f09d8c24b6389e3f451d467b38f6359ed'},
    'bench-2m': {'path': 'pool-tests/bench-2m.jpg', 'size': 2112525,
                 'sha': 'cb7926ffb9bb13c6a48efffd22412353ea6450d322feb6be1147ee6f204b6530'},
    'pool-public-smoke-test': {
        'path': 'submissions/originals/pool-public-smoke-test.jpg', 'size': 1778514,
        'sha': 'b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e'},
}


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


def run_client(seq, asset, workers, out, assets, scheduler=None):
    f = FILES[asset]
    m = out / 'metrics' / f'{seq}.jsonl'
    cmd = [sys.executable, '-m', 'pool.fetch', asset, str(out / 'dl.bin'),
           '--assets', str(assets), '--workers', str(workers), '--metrics', str(m)]
    if scheduler:
        cmd += ['--scheduler', scheduler]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                       errors='replace', timeout=900)
    wall = time.perf_counter() - t0
    sha_ok = False
    if (out / 'dl.bin').exists():
        sha_ok = sha256_file(out / 'dl.bin') == f['sha']
        (out / 'dl.bin').unlink()
    mode = None
    for line in (p.stdout or '').splitlines():
        if '模式：' in line:
            mode = line.split('模式：')[1].split('；')[0].strip()
    return {'arm': ('D' if scheduler is not None else 'B'), 'wall_ms': round(wall * 1000, 2),
            'sha_ok': sha_ok, 'mode': mode, 'rc': p.returncode,
            'err': (p.stderr or '')[-160:] if p.returncode else ''}


def run_curl_range(seq, asset, workers, out):
    f = FILES[asset]
    url = f'https://dl.bszx.site/{f["path"]}'
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
    return {'arm': 'C', 'wall_ms': round(wall * 1000, 2), 'sha_ok': sha_ok,
            'mode': 'curl-range', 'rc': rcs,
            'seg_speeds': [s.split()[0] for s in speeds],
            'http_codes': [s.split()[2] if len(s.split()) > 2 else '?' for s in speeds]}


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
        print(f'  {arm} w{w}: n={len(rs)} 中位={statistics.median(m):.2f} Mbps '
              f'范围={min(m):.2f}-{max(m):.2f} P90墙钟={p90:.0f}ms '
              f'最差={max(m):.2f} Mbps SHA={sum(r["sha_ok"] for r in rs)}/{len(rs)}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asset', default='bench-6m', choices=sorted(FILES))
    ap.add_argument('--assets', default=str(ASSETS),
                    help='trusted whitelist YAML; bench fixtures live in a '
                         'maintainer-side list, not the example file')
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--pairs', type=int, default=5, help='rounds of interleaved arms')
    ap.add_argument('--arms', default='A,B,C,D')
    ap.add_argument('--scheduler', default='dynamic', help='arm D scheduler value')
    ap.add_argument('--gap', type=float, default=2.0, help='seconds between runs')
    ap.add_argument('--out', default=str(REPO / 'reports' / 'benchmarks' / 'v05-pairs'))
    args = ap.parse_args()
    out = Path(args.out)
    (out / 'metrics').mkdir(parents=True, exist_ok=True)
    arms = [a.strip().upper() for a in args.arms.split(',')]

    runs_path = out / 'runs.jsonl'
    seq = 0
    for rnd in range(args.pairs):
        order = arms if rnd % 2 == 0 else arms[::-1]
        for arm in order:
            seq += 1
            rec = {'seq': seq, 'round': rnd, 'arm': arm, 'asset': args.asset,
                   'workers': 1 if arm == 'A' else args.workers,
                   'ts': round(time.time(), 3)}
            try:
                if arm == 'A':
                    rec.update(run_client(seq, args.asset, 1, out, args.assets))
                    rec['arm'] = 'A'
                elif arm == 'B':
                    rec.update(run_client(seq, args.asset, args.workers, out, args.assets))
                elif arm == 'C':
                    rec.update(run_curl_range(seq, args.asset, args.workers, out))
                elif arm == 'D':
                    rec.update(run_client(seq, args.asset, args.workers, out, args.assets,
                                          scheduler=args.scheduler))
                else:
                    raise ValueError(f'unknown arm {arm}')
            except Exception as exc:  # keep the round going; record and continue
                rec['error'] = f'{exc.__class__.__name__}: {exc}'[:200]
            with open(runs_path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(json.dumps(rec, ensure_ascii=False), flush=True)
            time.sleep(args.gap)
    runs = [json.loads(l) for l in open(runs_path, encoding='utf-8')]
    runs = [r for r in runs if r['asset'] == args.asset and r['workers'] == args.workers]
    summarize(runs)


if __name__ == '__main__':
    main()
