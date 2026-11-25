"""Metrics persistence guarantees: no silent overwrite, unique run ids,
complete files only."""
from __future__ import annotations

import json
import threading

import pytest

from pool import fetch, range_transport as rt


def _records(path):
    return [json.loads(line) for line in
            path.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_dump_refuses_to_overwrite_existing_file(tmp_path):
    path = tmp_path / 'metrics.jsonl'
    first = rt.Metrics(run_id='run-first')
    first.add({'kind': 'request', 'n': 1})
    first.dump(path)
    before = path.read_bytes()

    second = rt.Metrics(run_id='run-second')
    second.add({'kind': 'request', 'n': 2})
    with pytest.raises(FileExistsError, match='拒绝覆盖'):
        second.dump(path)
    assert path.read_bytes() == before
    assert [r['run_id'] for r in _records(path)] == ['run-first']


def test_run_ids_are_unique_and_stamped_on_every_record(tmp_path):
    a, b = rt.Metrics(), rt.Metrics()
    a.add({'kind': 'request', 'n': 1})
    a.add({'kind': 'download', 'n': 2})
    b.add({'kind': 'request', 'n': 3})
    assert a.run_id != b.run_id
    assert len(a.run_id) >= 20
    assert {r['run_id'] for r in a.records} == {a.run_id}
    assert b.records[0]['run_id'] == b.run_id

    a.dump(tmp_path / 'a.jsonl')
    b.dump(tmp_path / 'b.jsonl')
    assert _records(tmp_path / 'a.jsonl')[1]['run_id'] == a.run_id
    assert _records(tmp_path / 'b.jsonl')[0]['run_id'] == b.run_id


def test_explicit_run_id_is_used(tmp_path):
    metrics = rt.Metrics(run_id='harness-0001.007')
    metrics.add({'kind': 'download'})
    metrics.dump(tmp_path / 'm.jsonl')
    assert _records(tmp_path / 'm.jsonl')[0]['run_id'] == 'harness-0001.007'


def test_failed_serialization_leaves_no_final_file(tmp_path):
    path = tmp_path / 'metrics.jsonl'
    metrics = rt.Metrics(run_id='bad-run')
    metrics.add({'kind': 'request', 'payload': object()})  # not JSON serializable
    with pytest.raises(TypeError):
        metrics.dump(path)
    assert not path.exists()
    assert not list(tmp_path.glob('.*.tmp'))


def test_concurrent_dump_same_path_exactly_one_wins(tmp_path):
    path = tmp_path / 'metrics.jsonl'
    n_records = 40
    metrics = [rt.Metrics(run_id=f'racer-{i}') for i in range(2)]
    for i, m in enumerate(metrics):
        for j in range(n_records if i == 0 else n_records // 2):
            m.add({'kind': 'request', 'i': i, 'j': j})
    barrier = threading.Barrier(3)
    errors = []

    def worker(m):
        barrier.wait()
        try:
            m.dump(path)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(m,)) for m in metrics]
    for t in threads:
        t.start()
    barrier.wait()
    for t in threads:
        t.join()
    assert len(errors) == 1 and isinstance(errors[0], FileExistsError)
    rows = _records(path)
    winner = metrics[0] if rows[0]['run_id'] == metrics[0].run_id else metrics[1]
    assert len(rows) == len(winner.records)
    assert {r['run_id'] for r in rows} == {winner.run_id}


def test_fetch_refuses_existing_metrics_before_any_work(monkeypatch, tmp_path):
    path = tmp_path / 'metrics.jsonl'
    path.write_text('{"old": true}\n', encoding='utf-8')
    monkeypatch.setattr(fetch, 'select', lambda *a: pytest.fail('must not select'))
    monkeypatch.setattr(fetch, 'download', lambda *a: pytest.fail('must not download'))
    out = tmp_path / 'out.jpg'
    rc = fetch.main(['asset', str(out), '--assets', 'any.yaml',
                     '--metrics', str(path)])
    assert rc == 1
    assert not out.exists()
    assert path.read_text(encoding='utf-8') == '{"old": true}\n'


def test_fetch_metrics_carry_run_id(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, 'select',
                        lambda *a: ('https://dl.bszx.site/x.jpg', 'a' * 64, 1024))

    def fake_download(url, size, digest, output, workers, metrics, scheduler):
        metrics.add({'kind': 'request', 'label': 'probe'})
        return 'single'

    monkeypatch.setattr(fetch, 'download', fake_download)
    path = tmp_path / 'metrics.jsonl'
    rc = fetch.main(['asset', str(tmp_path / 'out.jpg'), '--assets', 'any.yaml',
                     '--metrics', str(path), '--run-id', 'fetch-run-1'])
    assert rc == 0
    rows = _records(path)
    assert {r['run_id'] for r in rows} == {'fetch-run-1'}
    assert rows[-1]['kind'] == 'download'
    assert rows[-1]['scheduler'] == 'static'  # 1024 B -> auto resolves to static
