#!/usr/bin/env python3
"""PoolNode 定点测速探针：单文件 single / w16 / w32 三臂聚合吞吐。

口径（2026-11-22 勘误后的可信方法）：
  * 全部为 **单文件** 测量——多文件整传数据不构成单文件分片证据；
  * 吞吐一律 **聚合**：总字节 × 8 ÷ 墙钟 ÷ 1e6（绝不把单流均摊当聚合）；
  * 每个 arm 都做 SHA256 校验：分段按字节偏移写入预分配文件，
    不做任何文件名排序（规避 seg_* 词法序陷阱）；
  * 三臂 **顺序** 执行、绝不同时压链路；执行顺序按 2 小时窗口奇偶交替，
    消除固定顺序与窗口漂移的偏置；
  * w32 为传输层容量探针：5.4MB 资产下每段 ~165KiB，低于 256KiB 段底，
    读数含已知握手开销，跨时间可比但不代表产品交付速度；
  * 记录每段耗时分布与尾比（slowest / median），用于尾等待分析。

安全：仅 HTTPS + 系统证书校验；3xx 拒绝；非 200/206 拒绝；identity 编码；
Content-Length / Content-Range / 强 ETag / If-Range 全校验；不写凭据。

用法：
  python scripts/peak_probe.py --tag local --out <jsonl> --slot-guard --enforce-window
  python scripts/peak_probe.py --tag cn2box --force   # 手动验证（跳过守卫）
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import ssl
import sys
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import budget_guard  # noqa: E402

# 仅当 runner 传入 POOL_BUDGET_LIMIT 时启用；否则为 None，行为与之前完全一致
BUDGET = budget_guard.from_env()

DEFAULT_ORIGIN = 'https://dl.bszx.site'
HOST = urlsplit(DEFAULT_ORIGIN).hostname
PORT = 443
STRONG = re.compile(r'"[\x21\x23-\x7e\x80-\xff]*"')
CHUNK = 64 * 1024


def configure_origin(value: str) -> None:
    """Set the probe target; standalone script, so parsing lives here.

    Deployed alone on the CN2 box: it must not depend on the pool package.
    """
    global HOST, PORT
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise SystemExit(f'invalid --origin (https://host[:port] required): {value}')
    HOST = parts.hostname
    PORT = parts.port or 443

# 自生成测试图（无第三方版权），SHA256 为生成器登记值。
FIXTURES = {
    'bench-6m': ('/pool-tests/bench-6m.jpg', 5_403_720,
                 '294d65cc0751226ef6691e5645f5625f09d8c24b6389e3f451d467b38f6359ed'),
    'bench-2m': ('/pool-tests/bench-2m.jpg', 2_112_525,
                 'cb7926ffb9bb13c6a48efffd22412353ea6450d322feb6be1147ee6f204b6530'),
    'smoke': ('/submissions/originals/pool-public-smoke-test.jpg', 1_778_514,
              'b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e'),
}

# 采样时段（香港时间）：每 2 小时一档 + 20–23 点逐小时
SLOT_HOURS = frozenset((0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 21, 22, 23))
DEFAULT_WINDOW = '2026-11-23T02:00/2026-11-24T23:59'


class ProbeError(Exception):
    pass


def hk_now() -> datetime:
    """香港时间（与两家服务器时区语义一致）；无 zoneinfo 时退回本地时区。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Hong_Kong'))
    except Exception:
        return datetime.now().astimezone()


def chunk_spans(size: int, n: int) -> list[tuple[int, int]]:
    """n 等分：与 v0.4 客户端相同的整数除法边界，无缝无重叠。"""
    n = max(1, min(n, size))
    return [(size * i // n, size * (i + 1) // n - 1) for i in range(n)]


def aggregate_mbps(nbytes: int, wall_s: float) -> float:
    return nbytes * 8 / 1e6 / wall_s if wall_s > 0 else 0.0


def _fetch(ctx, path, size, span=None, etag=None, sink=None, timeout=60.0):
    """一次 GET，全新连接（冷连接口径），逐项校验，返回 ETag。"""
    headers = {'Accept-Encoding': 'identity', 'Connection': 'close'}
    start = end = None
    if span is not None:
        start, end = span
        headers['Range'] = f'bytes={start}-{end}'
        if etag is not None:
            headers['If-Range'] = etag
        expected = end - start + 1
    else:
        expected = size
    if BUDGET is not None:
        # 在任何连接建立之前预留；不足则这里直接抛 BudgetExceeded
        BUDGET.reserve(expected)
    conn = http.client.HTTPSConnection(HOST, PORT, timeout=timeout, context=ctx)
    try:
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        with response:
            if 300 <= response.status < 400:
                raise ProbeError('redirect forbidden')
            if response.status not in (200, 206):
                raise ProbeError(f'HTTP {response.status}')
            if span is not None and response.status != 206:
                raise ProbeError(f'expected 206, got {response.status}')
            if span is None and response.status != 200:
                raise ProbeError(f'expected 200, got {response.status}')
            if (response.getheader('Content-Encoding') or 'identity').lower() != 'identity':
                raise ProbeError('encoded response')
            cl = response.getheader('Content-Length')
            if cl is not None and int(cl) != expected:
                raise ProbeError('content-length mismatch')
            tag = response.getheader('ETag')
            if span is not None:
                cr = response.getheader('Content-Range')
                if cr != f'bytes {start}-{end}/{size}':
                    raise ProbeError('content-range mismatch')
                if not tag or not STRONG.fullmatch(tag):
                    raise ProbeError('weak or missing ETag')
                if etag is not None and tag != etag:
                    raise ProbeError('ETag changed')
            if sink is not None:
                sink.seek(0 if span is None else start)
            remaining = expected
            while remaining:
                block = response.read(min(CHUNK, remaining))
                if not block:
                    raise ProbeError('short body')
                if sink is not None:
                    sink.write(block)
                remaining -= len(block)
            if response.read(1):
                raise ProbeError('oversized body')
            return tag
    finally:
        if BUDGET is not None:
            # 保守计入：无论成功/失败，预留额都算已消耗（不猜"字节没发出"）
            BUDGET.commit(expected)
        conn.close()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run_single(ctx, path, size, digest, tmpdir, timeout):
    tmp = Path(tmpdir) / 'single.bin'
    t0 = time.perf_counter()
    try:
        with open(tmp, 'wb') as f:
            _fetch(ctx, path, size, sink=f, timeout=timeout)
        wall = time.perf_counter() - t0
        ok = _sha256(tmp) == digest
        return {'arm': 'single', 'wall_ms': round(wall * 1000, 2),
                'mbps': round(aggregate_mbps(size, wall), 2),
                'sha_ok': ok, 'status': 'ok' if ok else 'sha-mismatch',
                'retries': 0, 'seg_count': 1,
                'error': None if ok else 'SHA256 mismatch'}
    except (ProbeError, OSError, http.client.HTTPException) as exc:
        wall = time.perf_counter() - t0
        return {'arm': 'single', 'wall_ms': round(wall * 1000, 2),
                'mbps': None, 'sha_ok': False, 'status': 'error',
                'retries': 0, 'seg_count': 1, 'error': str(exc)}
    finally:
        tmp.unlink(missing_ok=True)


def run_ranges(ctx, path, size, digest, nseg, tmpdir, timeout):
    """nseg 段并行（每段独立冷连接 + If-Range），偏移直写预分配文件后 SHA。"""
    tmp = Path(tmpdir) / f'w{nseg}.bin'
    t0 = time.perf_counter()
    arm = {'arm': f'w{nseg}', 'sha_ok': False, 'status': 'error',
           'retries': 0, 'seg_count': nseg, 'error': None, 'mbps': None}
    seg_ms: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    try:
        etag = _fetch(ctx, path, size, span=(0, 0), timeout=timeout)
        spans = chunk_spans(size, nseg)

        def work(span):
            start, end = span
            last = None
            for attempt in (1, 2):
                try:
                    t = time.perf_counter()
                    with open(tmp, 'r+b') as f:
                        _fetch(ctx, path, size, span, etag, f, timeout=timeout)
                    ms = (time.perf_counter() - t) * 1000
                    with lock:
                        seg_ms.append(ms)
                    return
                except (ProbeError, http.client.HTTPException, OSError) as exc:
                    last = exc
                    if attempt == 2:
                        break
                    with lock:
                        arm['retries'] += 1
            with lock:
                errors.append(str(last))

        with open(tmp, 'wb') as f:
            f.truncate(size)
        with ThreadPoolExecutor(max_workers=len(spans)) as pool:
            list(pool.map(work, spans))
        wall = time.perf_counter() - t0
        arm['wall_ms'] = round(wall * 1000, 2)
        if errors:
            arm['error'] = errors[0]
            return arm
        ok = _sha256(tmp) == digest
        arm['sha_ok'] = ok
        arm['status'] = 'ok' if ok else 'sha-mismatch'
        if ok:
            arm['mbps'] = round(aggregate_mbps(size, wall), 2)
        else:
            arm['error'] = 'SHA256 mismatch'
        if seg_ms:
            seg_bytes = size / len(spans)
            speeds = [aggregate_mbps(seg_bytes, ms / 1000) for ms in seg_ms]
            med = statistics.median(seg_ms)
            arm['segment_bytes'] = int(seg_bytes)
            arm['seg_mbps'] = {
                'min': round(min(speeds), 2),
                'median': round(statistics.median(speeds), 2),
                'max': round(max(speeds), 2)}
            arm['slowest_ms'] = round(max(seg_ms), 2)
            arm['tail_ratio'] = round(max(seg_ms) / med, 2) if med else None
            arm['segment_ms'] = [round(x, 1) for x in seg_ms]
        return arm
    except (ProbeError, http.client.HTTPException, OSError) as exc:
        arm['wall_ms'] = round((time.perf_counter() - t0) * 1000, 2)
        arm['error'] = str(exc)
        return arm
    finally:
        tmp.unlink(missing_ok=True)


def parse_window(spec):
    first, last = spec.split('/')
    return datetime.fromisoformat(first), datetime.fromisoformat(last)


def parse_slots(spec: str) -> frozenset:
    """采样小时白名单；空串返回默认 SLOT_HOURS（每 2h + 20-23 每小时）。"""
    spec = (spec or '').strip()
    if not spec:
        return SLOT_HOURS
    hours = {int(x) for x in spec.split(',') if x.strip()}
    bad = sorted(h for h in hours if not 0 <= h <= 23)
    if bad:
        raise ValueError(f'invalid hour(s): {bad}')
    return frozenset(hours)


def _last_record(path, tag):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = [line for line in f if line.strip()]
        for line in reversed(lines):
            rec = json.loads(line)
            if rec.get('tag') == tag:
                return rec
    except (OSError, ValueError):
        pass
    return None


def _acquire_lock(lock_path, stale_s=900):
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


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='PoolNode 单文件聚合吞吐探针（single/w16/w32，SHA 校验）')
    ap.add_argument('--tag', default=platform.node() or 'local', help='测量点标识')
    ap.add_argument('--asset', default='bench-6m', choices=sorted(FIXTURES))
    ap.add_argument('--arms', default='single,w16,w32')
    ap.add_argument('--out', default='probe-results.jsonl')
    ap.add_argument('--timeout', type=float, default=240.0, help='单请求 socket 超时（秒）')
    ap.add_argument('--window', default=DEFAULT_WINDOW)
    ap.add_argument('--slots', default='',
                    help='采样小时白名单（逗号分隔）；缺省 = 每 2h + 20-23 每小时')
    ap.add_argument('--enforce-window', action='store_true', help='窗口外静默跳过')
    ap.add_argument('--slot-guard', action='store_true', help='非采样时段静默跳过')
    ap.add_argument('--no-dedupe', action='store_true', help='允许同一小时重复记录')
    ap.add_argument('--force', action='store_true', help='跳过全部守卫（手动验证用）')
    ap.add_argument('--origin', default=os.environ.get('POOL_ORIGIN', DEFAULT_ORIGIN),
                    help='测量目标 origin（默认 POOL_ORIGIN 环境变量或生产 origin）')
    args = ap.parse_args(argv)
    configure_origin(args.origin)

    now = hk_now()
    if not args.force:
        start, end = parse_window(args.window)
        if args.enforce_window and not (start <= now.replace(tzinfo=None) <= end):
            print(f'skip: outside window {args.window}')
            return 0
        if args.slot_guard and now.hour not in parse_slots(args.slots):
            print(f'skip: hour {now.hour} is not a sample slot')
            return 0
        if not args.no_dedupe:
            last = _last_record(args.out, args.tag)
            if last and str(last.get('ts', ''))[:13] == now.strftime('%Y-%m-%dT%H'):
                print(f"skip: slot {now.strftime('%Y-%m-%dT%H')} already recorded")
                return 0

    arms = [a.strip() for a in args.arms.split(',') if a.strip()]
    for arm in arms:
        if arm not in ('single', 'w16', 'w32'):
            ap.error(f'unknown arm {arm!r}')

    lock_path = Path(str(args.out) + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = _acquire_lock(lock_path)
    if fd is None:
        print('skip: another probe holds the lock')
        return 0
    try:
        path, size, digest = FIXTURES[args.asset]
        parity = int(now.timestamp() // 7200) % 2
        order = arms[::-1] if parity else arms[:]
        ctx = ssl.create_default_context()
        ctx.set_alpn_protocols(['http/1.1'])
        tmpdir = tempfile.mkdtemp(prefix='pool-probe-')
        runs = []
        try:
            for arm in order:
                if arm == 'single':
                    runs.append(run_single(ctx, path, size, digest, tmpdir, args.timeout))
                else:
                    runs.append(run_ranges(ctx, path, size, digest, int(arm[1:]),
                                           tmpdir, min(args.timeout, 90.0)))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        rec = {'ts': now.isoformat(timespec='seconds'),
               'epoch': round(time.time(), 3), 'tag': args.tag,
               'host': platform.node(), 'python': platform.python_version(),
               'asset': args.asset, 'path': path, 'size': size,
               'order': order, 'arms': runs}
        with open(args.out, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())
        parts = []
        for r in runs:
            m = r.get('mbps')
            parts.append(f"{r['arm']}={'%.2f' % m if m is not None else 'FAIL'}")
        print(f"[{rec['ts']}] {args.tag} {args.asset} " + ' | '.join(parts))
        return 0 if all(r['sha_ok'] for r in runs) else 2
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
