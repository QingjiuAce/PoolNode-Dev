#!/usr/bin/env python3
"""C_MIN experiment: 96 KiB vs 128 KiB minimum chunk on the 1.7 MiB fixture.

For each C_MIN and each target concurrency (single / w8 / w13 / w16) the plan
is n = min(workers, floor(size / C_MIN)) — so C_MIN actually constrains the
achievable chunk count (128 KiB caps the 1.7 MiB file at 13 chunks).

Reuses peak_probe's transport, verification and per-segment timing primitives.
Each arm is a pair: both C_MIN values run back-to-back (randomized A/B order)
inside the same worker group, so no other download can interleave within a
pair. Worker-group order is shuffled per rep. One JSONL record per arm, each
carrying run_id, the RNG seed, the realized execution sequence (`seq`) and a
unique arm_id.

Usage:
  python scripts/cmin_experiment.py --reps 5                 # healthy run now
  python scripts/cmin_experiment.py --only-hours 20,21,22,23 --dedupe-hour
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import shutil
import ssl
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import peak_probe as pp  # noqa: E402

SIZE, PATH, DIGEST = pp.FIXTURES['smoke'][1], pp.FIXTURES['smoke'][0], pp.FIXTURES['smoke'][2]
ARMS = [(1, 'single'), (8, 'w8'), (13, 'w13'), (16, 'w16')]


def plan(size: int, workers: int, cmin: int) -> list:
    n = max(1, min(workers, size // cmin))
    return pp.chunk_spans(size, min(n, size))


def slot_kind(now: datetime) -> str:
    return 'peak' if now.hour in (20, 21, 22, 23) else 'regular'


def new_run_id() -> str:
    return time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4)


def arm_id(run_id: str, rep: int, name: str, cmin: int) -> str:
    """Unique within one experiment run: rep + worker group + C_MIN."""
    return f'{run_id}:r{rep}:{name}:c{cmin}'


def build_order(cmins, arms, reps, rng=None):
    """Return [(rep, cmin, workers, name)] with C_MIN variants back-to-back."""
    rng = rng or random
    order = []
    for rep in range(reps):
        groups = list(arms)
        rng.shuffle(groups)
        for workers, name in groups:
            pair = list(cmins)
            rng.shuffle(pair)
            order.extend((rep, cmin, workers, name) for cmin in pair)
    return order


def already_ran(path: Path, hour_key: str) -> bool:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                rec = json.loads(line)
                if str(rec.get('ts', ''))[:13] == hour_key:
                    return True
    except (OSError, ValueError):
        pass
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='C_MIN 96/128 KiB 专项实验（1.7MiB）')
    ap.add_argument('--reps', type=int, default=5)
    ap.add_argument('--cmin', default='98304,131072', help='逗号分隔的字节数')
    ap.add_argument('--out', default=str(Path(__file__).resolve().parents[1] /
                                         'reports' / 'benchmarks' / 'cmin-experiment' / 'runs.jsonl'))
    ap.add_argument('--only-hours', default='', help='仅这些小时运行（逗号分隔）')
    ap.add_argument('--dedupe-hour', action='store_true', help='本小时已跑过则跳过')
    ap.add_argument('--seed', type=int, default=None,
                    help='固定随机种子（正式窗口由授权指定；默认每次随机）')
    ap.add_argument('--timeout', type=float, default=120.0)
    ap.add_argument('--origin', default=os.environ.get('POOL_ORIGIN', pp.DEFAULT_ORIGIN),
                    help='内容源 origin（默认 POOL_ORIGIN 环境变量或生产 origin）')
    args = ap.parse_args(argv)
    pp.configure_origin(args.origin)

    now = pp.hk_now()
    if args.only_hours:
        allowed = {int(x) for x in args.only_hours.split(',') if x.strip()}
        if now.hour not in allowed:
            print(f'skip: hour {now.hour} not in --only-hours')
            return 0
    out = Path(args.out)
    if args.dedupe_hour and already_ran(out, now.strftime('%Y-%m-%dT%H')):
        print('skip: this hour already sampled')
        return 0

    cmins = [int(x) for x in args.cmin.split(',') if x.strip()]
    out.parent.mkdir(parents=True, exist_ok=True)
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(['http/1.1'])
    tmpdir = tempfile.mkdtemp(prefix='cmin-')
    run_id = new_run_id()
    # 随机化必须可复现（PN-EXPERIMENT-01 §3.2）：显式播种并记录到每条记录
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    rng = random.Random(seed)
    rc = 0
    print(f'run_id={run_id} seed={seed}', flush=True)
    try:
        for seq, (rep, cmin, workers, name) in enumerate(
                build_order(cmins, ARMS, args.reps, rng)):
            spans = plan(SIZE, workers, cmin)
            rec = {'ts': now.isoformat(timespec='seconds'),
                   'epoch': round(time.time(), 3), 'run_id': run_id, 'seed': seed, 'seq': seq,
                   'arm_id': arm_id(run_id, rep, name, cmin),
                   'slot_kind': slot_kind(now), 'rep': rep,
                   'cmin': cmin, 'workers': workers, 'arm': name,
                   'chunks': len(spans),
                   'chunk_bytes': SIZE // len(spans),
                   'asset': 'smoke', 'size': SIZE}
            try:
                if name == 'single':
                    arm = pp.run_single(ctx, PATH, SIZE, DIGEST, tmpdir, args.timeout)
                else:
                    arm = pp.run_ranges(ctx, PATH, SIZE, DIGEST, len(spans), tmpdir,
                                        args.timeout)
                rec.update(arm)
                rec['arm'] = name  # run_ranges labels itself w<chunks>; keep the arm id
                rec['requests'] = len(spans) + (0 if name == 'single' else 1)
            except Exception as exc:  # noqa: BLE001
                rec.update({'sha_ok': False, 'status': 'error', 'mbps': None,
                            'error': f'{exc.__class__.__name__}: {exc}'[:200]})
                rc = 1
            with open(out, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            print(f"rep{rep} cmin={cmin} {name}: chunks={rec['chunks']} "
                  f"chunk={rec['chunk_bytes']}B mbps={rec.get('mbps')} "
                  f"sha={rec.get('sha_ok')}", flush=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
