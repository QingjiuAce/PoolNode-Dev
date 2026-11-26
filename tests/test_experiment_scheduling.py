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
    monkeypatch.setattr(runner, "AUTH_DIR", logs / "authorizations")
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


def test_dirty_code_blocks_formal_but_not_smoke(monkeypatch):
    monkeypatch.setattr(runner, "code_dirty", lambda: ["M scripts/x.py"])
    formal = runner.precheck("cmin", runner.TASKS["cmin"], None, {"skip_origin": True})
    assert any(p.startswith("code_dirty") for p in formal)
    smoke = runner.precheck("cmin", runner.TASKS["cmin"], None,
                            {"skip_origin": True}, formal=False)
    assert not any(p.startswith("code_dirty") for p in smoke)


def test_execute_records_locks_and_uses_the_validated_path(monkeypatch, tmp_path):
    """一次真实执行：锁必须写进状态，--out 必须落在校验的同一个文件上。"""
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    script = tmp_path / "fake_task.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "ap = argparse.ArgumentParser(); ap.add_argument('--out'); a = ap.parse_args()\n"
        "p = pathlib.Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps({'ts': '2026-11-27T12:15:00', 'arm': 'B', 'round': 0}) + '\\n')\n",
        encoding="utf-8")
    task = dict(runner.TASKS["rcv05"])
    task.update(script=str(script), args=[], smoke_args=[], suspended=False,
                out_style="file", output=str(tmp_path / "out" / "runs.jsonl"))
    fixed = dt.datetime(2026, 11, 27, 20, 25, tzinfo=BJ)
    monkeypatch.setattr(runner, "now", lambda: fixed)
    st = runner.execute("rcv05", task, "rcv05-locks-test",
                        override={"skip_origin": True})
    assert st["status"] == "SUCCESS", st
    assert {item["resource_id"] for item in st["locks"]} == {"link:local-hk", "out:v05-rc1"}
    assert pathlib.Path(st["out_arg"]) == pathlib.Path(task["output"])
    saved = json.loads((runner.STATE_DIR / "rcv05" / "rcv05-locks-test.json")
                       .read_text(encoding="utf-8"))
    assert saved["status"] == "SUCCESS"
    assert {item["resource_id"] for item in saved["locks"]} == {"link:local-hk", "out:v05-rc1"}


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
# 一次性窗口授权（§A5）
# --------------------------------------------------------------------------
def _fake_formal_task(tmp_path, *, downloads=40, bytes_each=1_778_514):
    script = tmp_path / "fake_task.py"
    script.write_text(
        "import argparse, json, pathlib\n"
        "ap = argparse.ArgumentParser(); ap.add_argument('--out'); a = ap.parse_args()\n"
        "p = pathlib.Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps({'ts': '2026-11-27T12:15:00', 'arm': 'B', 'round': 0}) + '\\n')\n",
        encoding="utf-8")
    task = dict(runner.TASKS["rcv05"])
    task.update(script=str(script), args=[], smoke_args=[], suspended=False,
                hours={12, 20},
                out_style="file", output=str(tmp_path / "out" / "runs.jsonl"),
                requires_authorization=True,
                plan={"downloads": downloads, "bytes_each": bytes_each})
    return task


def _write_auth(hour=12, minute=15, max_bytes=120_000_000, **extra):
    runner.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    data = {"task": "rcv05", "date": "2026-11-27", "hour": hour, "minute": minute,
            "timezone": "Asia/Shanghai", "max_bytes": max_bytes, "single_use": True,
            "seed": 20261127,
            "approved_by": "test", "approved_at": "2026-11-27T11:30:00+08:00"}
    data.update(extra)
    runner.AUTH_DIR.joinpath(f"rcv05-2026-11-27T{hour:02d}.json").write_text(
        json.dumps(data), encoding="utf-8")


def test_no_authorization_blocks_the_run(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    monkeypatch.setattr(runner, "now",
                        lambda: dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ))
    st = runner.execute("rcv05", _fake_formal_task(tmp_path), "no-auth-test",
                        override={"skip_origin": True})
    assert st["status"] == "SKIPPED"
    assert st["error_summary"].startswith("no_authorization")
    assert st.get("record_count", 0) == 0


def test_authorization_is_single_use(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    monkeypatch.setattr(runner, "now",
                        lambda: dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ))
    _write_auth()
    task = _fake_formal_task(tmp_path)
    first = runner.execute("rcv05", task, "auth-first", override={"skip_origin": True})
    assert first["status"] == "SUCCESS", first
    assert first["budget"]["downloaded_bytes"] == 1_778_514
    marker = list(runner.AUTH_DIR.glob("*.consumed.json"))
    assert len(marker) == 1
    assert json.loads(marker[0].read_text(encoding="utf-8"))["run_id"] == "auth-first"
    second = runner.execute("rcv05", task, "auth-second", override={"skip_origin": True})
    assert second["status"] == "SKIPPED"
    assert second["error_summary"].startswith("authorization_already_consumed")


def test_authorization_must_match_the_window(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    monkeypatch.setattr(runner, "now",
                        lambda: dt.datetime(2026, 11, 27, 20, 15, tzinfo=BJ))
    _write_auth(hour=12)  # 12 点的授权不能在 20:15 使用
    st = runner.execute("rcv05", _fake_formal_task(tmp_path), "wrong-hour",
                        override={"skip_origin": True})
    assert st["status"] == "SKIPPED"
    assert st["error_summary"].startswith("no_authorization")


def test_budget_overrun_is_refused_before_consuming(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    monkeypatch.setattr(runner, "now",
                        lambda: dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ))
    _write_auth(max_bytes=1_000_000)  # 低于 40×1.78 MB
    st = runner.execute("rcv05", _fake_formal_task(tmp_path), "budget-test",
                        override={"skip_origin": True})
    assert st["status"] == "FAILED"
    assert st["error_summary"].startswith("budget_exceeds_authorization")
    assert not list(runner.AUTH_DIR.glob("*.consumed.json"))  # 未消费，可重试同一窗口


def test_upload_accepts_key_auth_without_password(monkeypatch, tmp_path):
    """配置里只有 key 没有 password 时不得抛 KeyError（2026-11-27 起的默认形态）。"""
    known = tmp_path / "known_hosts"
    known.write_text("", encoding="utf-8")
    conf = tmp_path / "remote.json"
    conf.write_text(json.dumps({"host": "127.0.0.1", "port": 1, "user": "x",
                                "known_hosts": str(known),
                                "key": str(tmp_path / "missing_key")}), encoding="utf-8")
    monkeypatch.setattr(runner, "REMOTE_CONF", conf)
    res = runner.remote_upload([(tmp_path / "f", "f")], "/tmp/x")
    assert res["enabled"] is True
    assert res["detail"].startswith("connect_failed:")


# --------------------------------------------------------------------------
# 进程内流量硬预算（RUN-02 §A4）
# --------------------------------------------------------------------------
def test_budget_reserve_commit_and_hard_stop():
    import budget_guard
    b = budget_guard.Budget(10)
    b.reserve(6); b.commit(6)
    b.reserve(4); b.commit(4)
    with pytest.raises(budget_guard.BudgetExceeded):
        b.reserve(1)
    assert b.stopped and b.spent == 10
    with pytest.raises(budget_guard.BudgetExceeded):
        b.reserve(0)          # 终止后连 0 字节也不放行


def test_budget_is_thread_safe():
    import budget_guard
    import threading
    b = budget_guard.Budget(1000)
    wins = []

    def worker():
        for _ in range(50):
            try:
                b.reserve(100)
                wins.append(1)
            except budget_guard.BudgetExceeded:
                return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 10                 # 恰好 10 次通过，没有超卖
    assert b.reserved + b.spent == 1000


def test_budget_state_file_is_written(tmp_path):
    import budget_guard
    state = tmp_path / "budget.json"
    b = budget_guard.Budget(100, str(state))
    b.reserve(60); b.commit(60)
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["limit"] == 100 and data["spent"] == 60 and data["stopped"] is False
    with pytest.raises(budget_guard.BudgetExceeded):
        b.reserve(41)
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["stopped"] is True and "budget_exceeded" in data["stop_reason"]


def test_budget_dump_does_not_deadlock(tmp_path):
    """dump() 在持锁路径上被调用；改成非重入锁会在这里超时失败而不是挂住套件。"""
    import budget_guard
    import threading
    b = budget_guard.Budget(1000, str(tmp_path / "b.json"))
    done = []

    def work():
        b.reserve(10)
        b.commit(10)
        b.stop("manual")
        done.append(1)

    t = threading.Thread(target=work)
    t.start()
    t.join(5)
    assert done == [1], "budget dump deadlocked"


def test_fetch_refuses_before_connecting(monkeypatch):
    """预算耗尽时 _fetch 必须在建立连接之前抛错。"""
    import budget_guard
    import peak_probe as pp
    b = budget_guard.Budget(5)
    monkeypatch.setattr(pp, "BUDGET", b)
    opened = []
    monkeypatch.setattr(pp.http.client, "HTTPSConnection",
                        lambda *a, **k: opened.append(1))
    with pytest.raises(budget_guard.BudgetExceeded):
        pp._fetch(None, "/x", 100)
    assert opened == []


def _recorder_task(tmp_path, *, stopped: bool, exit_code: int):
    """假脚本记录 argv / 预算环境变量，并（可选）写一份终止态预算。"""
    record = tmp_path / "invocation.json"
    script = tmp_path / "fake_recorder.py"
    script.write_text(
        "import argparse, json, os, pathlib, sys\n"
        "ap = argparse.ArgumentParser()\n"
        "ap.add_argument('--out'); ap.add_argument('--seed', type=int, default=None)\n"
        "a, _ = ap.parse_known_args()\n"
        "p = pathlib.Path(a.out); p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text(json.dumps({'ts': '2026-11-27T12:15:00', 'cmin': 98304,\n"
        "                         'workers': 1, 'rep': 0, 'arm': 'single', 'round': 0,\n"
        "                         'sha_ok': True, 'status': 'ok'}) + '\\n')\n"
        f"pathlib.Path({str(record)!r}).write_text(json.dumps({{\n"
        "    'argv': sys.argv[1:],\n"
        "    'budget_limit': os.environ.get('POOL_BUDGET_LIMIT'),\n"
        "    'budget_state': os.environ.get('POOL_BUDGET_STATE')}), encoding='utf-8')\n"
        + (
            "st = os.environ.get('POOL_BUDGET_STATE')\n"
            "if st:\n"
            "    pathlib.Path(st).write_text(json.dumps({'limit': 120000000, 'spent': 120000000,\n"
            "        'reserved': 0, 'requests': 68, 'rejected': 1, 'stopped': True,\n"
            "        'stop_reason': 'budget_exceeded: projected 121000000 > limit 120000000'}), encoding='utf-8')\n"
            if stopped else ""
        )
        + f"raise SystemExit({exit_code})\n",
        encoding="utf-8")
    task = dict(runner.TASKS["cmin"])
    task.update(script=str(script), args=[], smoke_args=[], suspended=False,
                telemetry=False, hours={12}, requires_authorization=True,
                out_style="file", output=str(tmp_path / "out" / "runs.jsonl"),
                plan={"downloads": 40, "bytes_each": 1_778_514})
    return task, record


def test_runner_injects_seed_and_budget_env(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    monkeypatch.setattr(runner, "now",
                        lambda: dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ))
    runner.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    (runner.AUTH_DIR / "cmin-2026-11-27T12.json").write_text(json.dumps({
        "task": "cmin", "date": "2026-11-27", "hour": 12, "minute": 15,
        "timezone": "Asia/Shanghai", "max_bytes": 120_000_000, "single_use": True,
        "seed": 20261127, "approved_by": "test",
        "approved_at": "2026-11-27T11:30:00+08:00"}), encoding="utf-8")
    task, record = _recorder_task(tmp_path, stopped=False, exit_code=0)
    st = runner.execute("cmin", task, "seed-env-test", override={"skip_origin": True})
    assert st["status"] == "SUCCESS", st
    inv = json.loads(record.read_text(encoding="utf-8"))
    assert "--seed" in inv["argv"] and "20261127" in inv["argv"]
    assert inv["budget_limit"] == "120000000"
    assert inv["budget_state"].endswith("seed-env-test.json")


def test_budget_stop_marks_partial_but_keeps_data(monkeypatch, tmp_path):
    _dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "code_dirty", lambda: [])
    monkeypatch.setattr(runner, "REMOTE_CONF", tmp_path / "no-remote.json")
    monkeypatch.setattr(runner, "now",
                        lambda: dt.datetime(2026, 11, 27, 12, 15, tzinfo=BJ))
    runner.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    (runner.AUTH_DIR / "cmin-2026-11-27T12.json").write_text(json.dumps({
        "task": "cmin", "date": "2026-11-27", "hour": 12, "minute": 15,
        "timezone": "Asia/Shanghai", "max_bytes": 120_000_000, "single_use": True,
        "seed": 20261127, "approved_by": "test",
        "approved_at": "2026-11-27T11:30:00+08:00"}), encoding="utf-8")
    task, _ = _recorder_task(tmp_path, stopped=True, exit_code=1)
    st = runner.execute("cmin", task, "budget-stop-test", override={"skip_origin": True})
    assert st["status"] == "PARTIAL"
    assert st["error_summary"].startswith("budget_exhausted")
    assert st["record_count"] == 1                 # 已完成的数据保留
    assert st["budget_runtime"]["stopped"] is True


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
    out = wc.classify(rows, {"ts": "2026-11-27T21:14:00+08:00",
                             "ping_origin": {"loss_pct": 60},
                             "head": {"value": {"ttfb_ms": 5000}}}, when)
    assert out["verdict"] == "UNKNOWN"
    assert out["telemetry"]["fresh"] is True


def test_single_stream_collapse_is_degraded_even_if_median_is_fine():
    """11-25 21:05 的真实形态：w32 17 Mbps，single 0.87 Mbps。"""
    when = dt.datetime(2026, 11, 25, 21, 15, tzinfo=BJ)
    rows = [{"ts": "2026-11-25T21:05:03+08:00", "tag": "local", "host": "h",
             "arms": [{"arm": "single", "mbps": 0.87}, {"arm": "w16", "mbps": 9.37},
                      {"arm": "w32", "mbps": 17.33}]}]
    out = wc.classify(rows, None, when)
    assert out["verdict"] == "DEGRADED"
    assert out["probe"]["single_mbps"] == 0.87


def test_stale_telemetry_is_ignored():
    when = dt.datetime(2026, 11, 25, 21, 15, tzinfo=BJ)
    rows = [_probe("2026-11-25T21:05:01+08:00", 18.0)]
    tel = {"ts": "2026-11-27T01:40:00+08:00", "ping_origin": {"loss_pct": 60},
           "head": {"value": {"ttfb_ms": 5000}}}
    out = wc.classify(rows, tel, when)
    assert out["verdict"] == "HEALTHY"
    assert out["telemetry"]["fresh"] is False


def test_classify_event_tail_after_degraded_slot():
    when = dt.datetime(2026, 11, 27, 23, 15, tzinfo=BJ)
    rows = [_probe("2026-11-27T20:05:01+08:00", 0.4),
            _probe("2026-11-27T23:05:01+08:00", 25.0)]
    out = wc.classify(rows, None, when)
    assert out["verdict"] == "EVENT_TAIL"
