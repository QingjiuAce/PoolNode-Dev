"""Recover the two overwritten v0.5-RC metrics generations into separate dirs.

Provenance:
  health_window  <- git HEAD:reports/benchmarks/v05-rc1/metrics/<n>.jsonl
  event_window   <- working tree:reports/benchmarks/v05-rc1/metrics/<n>.jsonl

Both sources are read-only. Output files are created exclusively; an existing
identical copy is accepted, an existing different copy is a hard error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

FILES = [f'{i}.jsonl' for i in range(1, 11)]
TZ = dt.timezone(dt.timedelta(hours=8))


def head_bytes(repo: pathlib.Path, rel: str) -> bytes:
    return subprocess.run(
        ['git', 'show', f'HEAD:{rel}'], cwd=repo,
        capture_output=True, check=True).stdout


def analyse(name: str, data: bytes) -> dict:
    text = data.decode('utf-8')
    lines = [line for line in text.splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    stamps = [r['ts'] for r in records if isinstance(r.get('ts'), (int, float))]
    downloads = [r for r in records if r.get('kind') == 'download']
    kinds = {}
    for r in records:
        kinds[r.get('kind', '?')] = kinds.get(r.get('kind', '?'), 0) + 1
    ids = sorted({r.get('run_id') for r in records if r.get('run_id')})
    return {
        'file': name,
        'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'lines': len(lines),
        'kinds': kinds,
        'run_ids': ids,
        'first_ts': min(stamps) if stamps else None,
        'last_ts': max(stamps) if stamps else None,
        'first_local': dt.datetime.fromtimestamp(min(stamps), TZ).isoformat() if stamps else None,
        'last_local': dt.datetime.fromtimestamp(max(stamps), TZ).isoformat() if stamps else None,
        'download': downloads[-1] if downloads else None,
    }


def write_exclusive(path: pathlib.Path, data: bytes) -> str:
    if path.exists():
        if path.read_bytes() == data:
            return 'identical'
        raise SystemExit(f'refusing to replace a different existing file: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'xb') as f:  # exclusive create
        f.write(data)
    return 'created'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.')
    args = ap.parse_args()
    repo = pathlib.Path(args.repo).resolve()
    rel = 'reports/benchmarks/v05-rc1/metrics'
    out_root = repo / 'reports/benchmarks/v05-rc1/recovered'
    index = {'sources': {}, 'files': []}
    for window, reader in (
        ('health_window', lambda n: head_bytes(repo, f'{rel}/{n}')),
        ('event_window', lambda n: (repo / rel / n).read_bytes()),
    ):
        source = (f'git HEAD:{rel}' if window == 'health_window'
                  else f'working tree:{rel}')
        index['sources'][window] = {'source': source, 'files': len(FILES)}
        for n in FILES:
            data = reader(n)
            target = out_root / window / 'metrics' / n
            action = write_exclusive(target, data)
            meta = analyse(n, data)
            meta['window'] = window
            meta['source'] = source
            meta['archive_action'] = action
            index['files'].append(meta)
            print(f'{window}/{n}: {action} {meta["sha256"][:16]} '
                  f'{meta["lines"]} lines {meta["first_local"]} .. {meta["last_local"]}')
    index_path = out_root / 'recovery_index.json'
    payload = json.dumps(index, ensure_ascii=False, indent=2) + '\n'
    if index_path.exists():
        if index_path.read_text(encoding='utf-8') != payload:
            raise SystemExit(f'refusing to replace a different {index_path}')
    else:
        index_path.write_text(payload, encoding='utf-8')
    print(f'index: {index_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
