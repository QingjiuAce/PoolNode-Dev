#!/usr/bin/env python3
"""Periodic collector for CN2 box probe records (append-only, verified).

Contract (v0.6 task order TASK-B):
  * SSH/scp run with strict host-key checking and the pinned ED25519 key
    (BatchMode + HostKeyAlgorithms=ssh-ed25519); no password, no AutoAddPolicy.
  * The remote JSONL is never modified or deleted here.
  * Local archive is append-only: the remote file must be a byte-prefix
    extension of the local copy, otherwise the run fails without writing.
  * Every received payload is verified: remote sha256 == received sha256, and
    every appended line parses as a probe record (ts + arms).
  * Failures append an ERROR line to collect.log and exit non-zero.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOST = '160.202.47.187'
USER = 'root'
REMOTE_FILE = '/root/pool-probe/probe-results-v06.jsonl'
REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / 'reports' / 'benchmarks' / 'v06-monitoring' / 'cn2box-probe.jsonl'
LOG = REPO / 'reports' / 'benchmarks' / 'v06-monitoring' / 'collect.log'
SSH_OPTS = ['-o', 'BatchMode=yes', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'ConnectTimeout=15']


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {line}\n')


def ssh(command: str, timeout=60) -> tuple[int, str]:
    proc = subprocess.run(['ssh', *SSH_OPTS, f'{USER}@{HOST}', command],
                          capture_output=True, text=True, errors='replace',
                          timeout=timeout)
    return proc.returncode, (proc.stdout or '').strip()


def fetch_remote(tmp_path: Path) -> None:
    proc = subprocess.run(['scp', *SSH_OPTS, f'{USER}@{HOST}:{REMOTE_FILE}',
                           str(tmp_path)], capture_output=True, text=True,
                          errors='replace', timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f'scp failed: {(proc.stderr or "").strip()[:200]}')


def validate_jsonl(payload: bytes) -> int:
    """Every non-empty line must be a probe record with ts + arms."""
    count = 0
    for no, line in enumerate(payload.splitlines(), 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        if not isinstance(rec, dict) or 'ts' not in rec or 'arms' not in rec:
            raise ValueError(f'line {no}: missing ts/arms')
        count += 1
    return count


def merge_append(local_path: Path, remote_bytes: bytes) -> int:
    """Append only the new tail; require a byte-identical prefix."""
    local = local_path.read_bytes() if local_path.exists() else b''
    if local and not local.endswith(b'\n'):
        raise RuntimeError('local archive does not end at a line boundary')
    if not remote_bytes.startswith(local):
        raise RuntimeError('remote file diverges from local archive (not an extension)')
    payload = remote_bytes[len(local):]
    if payload:
        validate_jsonl(payload)
        with open(local_path, 'ab') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    return payload.count(b'\n')


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        rc, out = ssh(f'if [ -f {REMOTE_FILE} ]; then '
                      f'wc -l < {REMOTE_FILE}; sha256sum {REMOTE_FILE} | cut -d" " -f1; '
                      f'else echo MISSING; fi')
        if rc != 0:
            raise RuntimeError(f'ssh failed: {out[:200]}')
        if out == 'MISSING':
            log('OK remote-no-file (probe has not produced a record yet)')
            return 0
        lines = out.splitlines()
        if len(lines) != 2:
            raise RuntimeError(f'unexpected remote stat output: {out[:200]!r}')
        remote_lines, remote_sha = int(lines[0]), lines[1]

        local_lines = (ARCHIVE.read_bytes().count(b'\n') if ARCHIVE.exists() else 0)
        if local_lines == remote_lines:
            log(f'OK up-to-date lines={remote_lines} sha={remote_sha[:16]}')
            return 0
        if remote_lines < local_lines:
            raise RuntimeError(f'remote has fewer lines ({remote_lines}) than local ({local_lines})')

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / 'remote.jsonl'
            fetch_remote(tmp)
            data = tmp.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != remote_sha:
                raise RuntimeError(f'sha mismatch remote={remote_sha[:16]} got={digest[:16]}')
            added = merge_append(ARCHIVE, data)
        log(f'OK collected lines={remote_lines} (+{added}) sha={remote_sha[:16]}')
        return 0
    except Exception as exc:  # noqa: BLE001 - logged with explicit status
        log(f'ERROR {exc.__class__.__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
