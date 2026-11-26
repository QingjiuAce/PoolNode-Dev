"""调度互斥、输出路径绑定与窗口分类（PN-EXPERIMENT-01 阶段 1/2 验收）。

覆盖任务书 §2.6 要求的五类锁测试，外加：
  * 正式模式 --out 必须与校验路径一致（历史 R3/输出目录错位的回归测试）
  * cmin 未授权时不得产生流量
  * CRLF 归档仍可增量同步（不改历史字节）
  * 随机种子可复现
  * 窗口分类在证据不足时返回 UNKNOWN
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import pathlib
import random
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "experiment"))

import cmin_experiment as ce  # noqa: E402
import locks  # noqa: E402
import runner  # noqa: E402
import window_class as wc  # noqa: E402

CB_SCRIPT = REPO / "scripts" / "collect_box_probe.py"
_spec = importlib.util.spec_from_file_location("collect_box_probe_guard", CB_SCRIPT)
cb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cb)

BJ = dt.timezone(dt.timedelta(hours=8))


def _dirs(monkeypatch, tmp_path):
    logs = tmp_path / "logs"
    monkeypatch.setattr(runner, "LOGS", logs)
    monkeypatch.setattr(runner, "STATE_DIR", logs / "state")
    monkeypatch.setattr(runner, "SMOKE_DIR", logs / "smoke")
    monkeypatch.setattr(runner, "TELEMETRY_DIR", logs / "telemetry")
    monkeypatch.setattr(runner, "LOCK_DIR", logs / "locks")
    monkeypatch.setattr(runner, "LOCK_JOURNAL", logs / "locks.jsonl")
    return logs


# --------------------------------------------------------------------------
# 锁：并发 / 冲突 / 异常退出 / 清理 / 不按年龄判定
# --------------------------------------------------------------------------
def test_second_holder_gets_conflict(tmp_path):
    d, j = tmp_path / "locks", tmp_path / "locks.jsonl"
    first = locks.hold(d, j, ["link:local-hk"], "cmin", "run-1")
    try:
        with pytest.raises(locks.LockConflict) as excinfo:
            locks.hold(d, j, ["link:local-hk"], "rcv05", "run-2")
        reason = excinfo.value.reason()
        assert "link:local-hk" in reason and "cmin/run-1" in reason
    finally:
        first.release()
    after = locks.hold(d, j, ["link:local-hk"], "rcv05", "run-3")
    after.release()


def test_lock_released_when_holder_process_dies(tmp_path):
    d = tmp_path / "locks"
    j = tmp_path / "locks.jsonl"
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(REPO / 'scripts' / 'experiment')!r})\n"
        "import locks\n"
        f"locks.hold({str(d)!r}, {str(j)!r}, ['link:local-hk'], 'cmin', 'run-crash')\n"
        "print('held', flush=True)\n"
        "os._exit(9)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=60)
    assert "held" in proc.stdout
    # 内核在进程退出时释放锁：不需要等待任何超时或"锁文件年龄"
    again = locks.hold(d, j, ["link:local-hk"], "rcv05", "run-next")
    again.release()


def test_partial_acquisition_releases_everything(tmp_path):
    d, j = tmp_path / "locks", tmp_path / "locks.jsonl"
    blocker = locks.hold(d, j, ["out:b"], "probe", "run-b")
    try:
        with pytest.raises(locks.LockConflict):
            locks.hold(d, j, ["out:a", "out:b"], "cmin", "run-a")
    finally:
        blocker.release()
    # 已获取的 out:a 必须被释放，否则后续运行会被永久挡住
    for _ in range(2):
        again = locks.hold(d, j, ["out:a", "out:b"], "cmin", "run-a2")
        again.release()


def test_release_is_idempotent_and_journaled(tmp_path):
    d, j = tmp_path / "locks", tmp_path / "locks.jsonl"
    s = locks.hold(d, j, ["out:x"], "cmin", "run-j")
    s.release()
    s.release()
    states = [json.loads(line)["state"] for line in j.read_text(encoding="utf-8").splitlines()]
    assert states.count("acquired") == 1
    assert states.count("released") == 1


def test_stale_owner_metadata_never_blocks(tmp_path):
    """旧时间戳的元数据不得被当作"锁仍然有效"的证据。"""
    d = tmp_path / "locks"
    j = tmp_path / "locks.jsonl"
    d.mkdir()
    (d / "out_x.owner.json").write_text(json.dumps(
        {"task": "cmin", "run_id": "ancient", "pid": 1,
         "acquired_at": "2026-01-01T00:00:00+08:00"}), encoding="utf-8")
    lock = locks.hold(d, j, ["out:x"], "probe", "run-new")
    lock.release()
    owner = json.loads((d / "out_x.owner.json").read_text(encoding="utf-8"))
    assert owner["run_id"] == "run-new"


def test_invalid_resource_id_is_rejected(tmp_path):
    with pytest.raises(locks.LockError):
        locks.hold(tmp_path / "l", tmp_path / "j", ["../escape"], "cmin", "r")


# --------------------------------------------------------------------------
# 输出路径必须指向本仓库内的校验目标
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", sorted(runner.TASKS))
def test_out_argument_matches_validation_target(name):
    task = runner.TASKS[name]
    target = runner.target_path(task, None)
    arg = pathlib.Path(runner.out_target(task, target))
    expected = target.parent if task["out_style"] == "dir" else target
    assert arg == expected
    assert expected.is_relative_to(runner.ROOT)


def test_strip_out_removes_both_forms():
    assert runner.strip_out(["--a", "1", "--out", "x", "--b"]) == ["--a", "1", "--b"]
    assert runner.strip_out(["--out=x", "--b"]) == ["--b"]


def test_rcmatrix_never_writes_into_other_worktree():
    task = runner.TASKS["rcmatrix"]
    target = runner.target_path(task, None)
    arg = runner.out_target(task, target)
    assert "Pool-investigate" not in arg
    assert str(target).startswith(str(runner.ROOT))


# --------------------------------------------------------------------------
# 未授权的 C_MIN 不得产生流量
# --------------------------------------------------------------------------
def test_cmin_is_suspended_and_off_plan_hour_removed():
    task = runner.TASKS["cmin"]
    assert task["suspended"] is True
    assert 1 not in task["hours"]
    assert task["hours"] == {12, 20, 21, 22, 23}


def test_execute_refuses_suspended_task(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    fixed = dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ)
    monkeypatch.setattr(runner, "now", lambda: fixed)
    st = runner.execute("cmin", runner.TASKS["cmin"], "cmin-suspended-test")
    assert st["status"] == "SKIPPED"
    assert "suspended" in st["error_summary"]


def test_execute_skips_on_lock_conflict(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    fixed = dt.datetime(2026, 11, 27, 12, 5, tzinfo=BJ)
    monkeypatch.setattr(runner, "now", lambda: fixed)
    holder = locks.hold(runner.LOCK_DIR, runner.LOCK_JOURNAL, ["link:local-hk"],
                        "cmin", "run-holder")
    try:
        st = runner.execute("probe", runner.TASKS["probe"], "probe-lock-test")
    finally:
        holder.release()
    assert st["status"] == "SKIPPED"
    assert st["error_summary"].startswith("lock_conflict")
    assert st["record_count"] == 0


# --------------------------------------------------------------------------
# 采集器：CRLF 历史归档仍可增量，且历史字节不变
# --------------------------------------------------------------------------
def _rec(ts: str) -> bytes:
    return json.dumps({"ts": ts, "tag": "cn2box", "arms": []}).encode() + b"\n"


def test_merge_append_accepts_crlf_archive(tmp_path):
    archive = tmp_path / "cn2.jsonl"
    original = _rec("a").replace(b"\n", b"\r\n")
    archive.write_bytes(original)
    added = cb.merge_append(archive, _rec("a") + _rec("b"))
    assert added == 1
    assert archive.read_bytes() == original + _rec("b")  # 旧字节一个不改


def test_merge_append_still_rejects_real_divergence(tmp_path):
    archive = tmp_path / "cn2.jsonl"
    archive.write_bytes(_rec("a").replace(b"\n", b"\r\n"))
    with pytest.raises(RuntimeError, match="diverges"):
        cb.merge_append(archive, _rec("x") + _rec("b"))


# --------------------------------------------------------------------------
# C_MIN 随机种子可复现
# --------------------------------------------------------------------------
def test_seed_reproduces_order():
    a = ce.build_order([98304, 131072], ce.ARMS, 3, random.Random(42))
    b = ce.build_order([98304, 131072], ce.ARMS, 3, random.Random(42))
    c = ce.build_order([98304, 131072], ce.ARMS, 3, random.Random(43))
    assert a == b
    assert a != c


# --------------------------------------------------------------------------
# 任务入口一致性：唯一的可执行入口是 runner.py --task
# --------------------------------------------------------------------------
def test_install_script_uses_runner_for_every_task():
    text = (REPO / "scripts" / "experiment" / "install_tasks.ps1").read_text(encoding="utf-8")
    for task in ("cmin", "rcv05", "rcmatrix", "probe", "sync"):
        assert f'Task = "{task}"' in text
        assert task in runner.TASKS
    assert "--task " in text
    for script in ("cmin_experiment.py", "rc_matrix.py", "peak_probe.py",
                   "collect_box_probe.py"):
        assert script not in text


# --------------------------------------------------------------------------
# 窗口分类
# --------------------------------------------------------------------------
def _probe(ts: str, mbps: float) -> dict:
    return {"ts": ts, "tag": "local", "host": "h",
            "arms": [{"arm": "w16", "mbps": mbps}, {"arm": "single", "mbps": mbps * 0.5}]}


def test_classify_healthy():
    when = dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ)
    rows = [_probe("2026-11-27T12:05:02+08:00", 18.0)]
    out = wc.classify(rows, {"ping_origin": {"loss_pct": 0},
                             "head": {"value": {"ttfb_ms": 300}}}, when)
    assert out["verdict"] == "HEALTHY"


def test_classify_degraded():
    when = dt.datetime(2026, 11, 27, 21, 15, tzinfo=BJ)
    rows = [_probe("2026-11-27T21:05:01+08:00", 0.6)]
    out = wc.classify(rows, None, when)
    assert out["verdict"] == "DEGRADED"


def test_classify_unknown_without_probe():
    when = dt.datetime(2026, 11, 27, 21, 15, tzinfo=BJ)
    out = wc.classify([], None, when)
    assert out["verdict"] == "UNKNOWN"


def test_classify_conflicting_evidence_is_unknown():
    when = dt.datetime(2026, 11, 27, 21, 15, tzinfo=BJ)
    rows = [_probe("2026-11-27T21:05:01+08:00", 30.0)]
    out = wc.classify(rows, {"ping_origin": {"loss_pct": 60},
                             "head": {"value": {"ttfb_ms": 5000}}}, when)
    assert out["verdict"] == "UNKNOWN"


def test_classify_event_tail_after_degraded_slot():
    when = dt.datetime(2026, 11, 27, 23, 15, tzinfo=BJ)
    rows = [_probe("2026-11-27T20:05:01+08:00", 0.4),
            _probe("2026-11-27T23:05:01+08:00", 25.0)]
    out = wc.classify(rows, None, when)
    assert out["verdict"] == "EVENT_TAIL"
