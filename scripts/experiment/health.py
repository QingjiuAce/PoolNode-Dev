#!/usr/bin/env python3
"""实验任务健康检查与告警（PN-RECOVERY-01 阶段 2）。

读取 reports/experiment-logs/state/<task>/*.json，判断：
  * 连续失败次数
  * 最近一次成功是否超过预期窗口
  * 脚本/输出是否仍然存在

输出：
  reports/experiment-logs/alerts.current.json   （当前告警，覆盖）
  reports/experiment-logs/alerts.jsonl          （历史追加，仅状态变化）
  reports/experiment-logs/HEALTH.md             （人类可读面板）

退出码：0=OK 1=WARNING 2=CRITICAL
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOGS = ROOT / "reports" / "experiment-logs"
STATE_DIR = LOGS / "state"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from runner import TASKS  # noqa: E402

FAIL_STATES = {"FAILED", "TIMEOUT", "PARTIAL"}


def load_states(task: str) -> list[dict]:
    d = STATE_DIR / task
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda s: s.get("end_time") or s.get("start_time") or "")
    return out


def last_window(task: dict, moment: dt.datetime) -> dt.datetime:
    if task.get("hours") is None:
        cand = moment.replace(hour=23, minute=40, second=0, microsecond=0)
        return cand if cand <= moment else cand - dt.timedelta(days=1)
    for back in range(0, 96):
        cand = (moment - dt.timedelta(hours=back)).replace(minute=0, second=0, microsecond=0)
        if cand.hour in task["hours"]:
            return cand
    return moment


def recent(states: list[dict], now: dt.datetime, hours: int = 24) -> list[dict]:
    out = []
    for s in states:
        try:
            end = dt.datetime.fromisoformat(s.get("end_time") or s.get("start_time") or "")
        except ValueError:
            continue
        if dt.timedelta(0) <= now - end <= dt.timedelta(hours=hours):
            out.append(s)
    return out


def evaluate(now: dt.datetime) -> dict:
    findings = []
    for name, task in TASKS.items():
        # 烟测/注入产生的状态不算正式样本：否则一次烟测会把"从未成功"掩盖掉
        states = [s for s in load_states(name) if not s.get("smoke")]
        script = ROOT / task["script"]
        entry = {"task": name, "runs": len(states), "status": "UNKNOWN",
                 "consecutive_failures": 0, "last_success": None, "last_run": None,
                 "issues": []}

        if task.get("suspended"):
            # 暂停是显式状态，不是"最近没跑"：不参与总体级别，也不制造 missed_windows
            entry["issues"].append({"level": "INFO", "code": "suspended",
                                    "detail": task.get("suspend_reason", "已暂停")})
            entry["status"] = "SUSPENDED"
            findings.append(entry)
            continue

        if not script.is_file():
            entry["issues"].append({"level": "CRITICAL", "code": "script_missing",
                                    "detail": str(script)})

        if not states:
            entry["issues"].append({"level": "WARNING", "code": "no_runs_yet",
                                    "detail": "尚未产生任何运行记录"})
        else:
            last = states[-1]
            entry["last_run"] = {"run_id": last.get("run_id"), "status": last.get("status"),
                                 "end": last.get("end_time"),
                                 "summary": (last.get("error_summary") or "")[:160]}
            # 最新一条若一直停在 RUNNING，说明进程被强杀/崩溃
            if last.get("status") == "RUNNING" and last.get("start_time"):
                started = dt.datetime.fromisoformat(last["start_time"])
                limit = task.get("timeout", 1800) + 600
                if (now - started).total_seconds() > limit:
                    entry["issues"].append({
                        "level": "CRITICAL", "code": "stale_running",
                        "detail": f"run {last.get('run_id')} 自 {started:%m-%d %H:%M} 起停在 RUNNING",
                        "run_id": last.get("run_id")})

            consecutive = 0
            for st in reversed(states):
                if st.get("status") in FAIL_STATES:
                    consecutive += 1
                else:
                    break
            entry["consecutive_failures"] = consecutive
            if consecutive >= 2:
                entry["issues"].append({"level": "CRITICAL", "code": "repeated_failure",
                                        "detail": f"连续 {consecutive} 次失败；最后错误："
                                                  f"{(states[-1].get('error_summary') or '')[:160]}",
                                        "run_id": states[-1].get("run_id")})
            elif consecutive == 1:
                entry["issues"].append({"level": "WARNING", "code": "task_failed",
                                        "detail": (states[-1].get("error_summary") or "")[:160],
                                        "run_id": states[-1].get("run_id")})

            # 调度互斥冲突（§2.4 第 6/7 条）：单次可见，重复出现说明窗口真的重叠
            conflicts = [s for s in recent(states, now, hours=24)
                         if str(s.get("error_summary", "")).startswith("lock_conflict")]
            entry["lock_conflicts_24h"] = len(conflicts)
            if len(conflicts) >= 2:
                entry["issues"].append({
                    "level": "WARNING", "code": "repeated_lock_conflict",
                    "detail": f"24h 内 {len(conflicts)} 次锁冲突；最后一次："
                              f"{(conflicts[-1].get('error_summary') or '')[:160]}",
                    "run_id": conflicts[-1].get("run_id")})
            elif conflicts:
                entry["issues"].append({
                    "level": "INFO", "code": "lock_conflict",
                    "detail": (conflicts[-1].get("error_summary") or "")[:160],
                    "run_id": conflicts[-1].get("run_id")})

            # 未授权却被触发（一次性授权缺失/已消费）：必须可见，不能被当成普通 SKIPPED
            unauth = [s for s in recent(states, now, hours=24)
                      if str(s.get("error_summary", "")).startswith(
                          ("no_authorization", "authorization_invalid",
                           "authorization_already_consumed"))]
            if unauth:
                entry["issues"].append({
                    "level": "WARNING", "code": "unauthorized_trigger",
                    "detail": (unauth[-1].get("error_summary") or "")[:160],
                    "run_id": unauth[-1].get("run_id")})

            successes = [s for s in states if s.get("status") == "SUCCESS"]
            if successes:
                last_ok = dt.datetime.fromisoformat(successes[-1]["end_time"])
                entry["last_success"] = last_ok.isoformat()
                expected = last_window(task, now)
                missed = (expected - last_ok).total_seconds()
                if missed > 7200:
                    entry["issues"].append({
                        "level": "CRITICAL", "code": "missed_windows",
                        "detail": f"最近成功 {last_ok:%m-%d %H:%M}，已错过 {expected:%m-%d %H:%M} 窗口"})
                elif missed > 0:
                    entry["issues"].append({
                        "level": "WARNING", "code": "window_pending",
                        "detail": f"最近成功 {last_ok:%m-%d %H:%M}，本窗口 {expected:%m-%d %H:%M} 尚无成功记录"})
            else:
                # 只有"真的进过窗口"的运行才有资格谈成功/失败；窗口外 SKIPPED 不算
                eligible = [s for s in states
                            if not (s.get("status") == "SKIPPED"
                                    and str(s.get("error_summary", "")).startswith("hour "))]
                if not eligible:
                    entry["issues"].append({
                        "level": "INFO", "code": "no_window_yet",
                        "detail": "自恢复以来还没有落在采样窗口内的正式运行"})
                elif task.get("requires_authorization"):
                    entry["issues"].append({
                        "level": "WARNING", "code": "awaiting_authorized_window",
                        "detail": "已配置一次性授权，尚未到（或尚未完成）授权窗口"})
                else:
                    entry["issues"].append({"level": "CRITICAL", "code": "never_succeeded",
                                            "detail": "有窗口内运行记录但从未成功"})

        levels = {i["level"] for i in entry["issues"]}
        entry["status"] = ("CRITICAL" if "CRITICAL" in levels else
                           "WARNING" if "WARNING" in levels else "OK")
        findings.append(entry)

    overall = ("CRITICAL" if any(f["status"] == "CRITICAL" for f in findings) else
               "WARNING" if any(f["status"] == "WARNING" for f in findings) else "OK")
    return {"checked_at": now.isoformat(), "overall": overall, "tasks": findings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PoolNode experiment health check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    now = dt.datetime.now().astimezone()
    report = evaluate(now)
    LOGS.mkdir(parents=True, exist_ok=True)

    current = LOGS / "alerts.current.json"
    previous = {}
    if current.is_file():
        try:
            previous = json.loads(current.read_text(encoding="utf-8"))
        except ValueError:
            previous = {}
    current.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 仅当总体级别或某任务状态变化时追加历史，避免每小时刷屏
    sig = json.dumps({f["task"]: f["status"] for f in report["tasks"]}, sort_keys=True)
    prev_sig = json.dumps({f["task"]: f["status"] for f in previous.get("tasks", [])}, sort_keys=True) \
        if previous.get("tasks") else ""
    if sig != prev_sig:
        with (LOGS / "alerts.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False) + "\n")

    lines = [f"# PoolNode 实验采集健康 — {now:%Y-%m-%d %H:%M}", "",
             f"总体：**{report['overall']}**", "",
             "| 任务 | 状态 | 最近运行 | 连续失败 | 最近成功 | 问题 |",
             "|---|---|---|---:|---|---|"]
    for f in report["tasks"]:
        issue = "; ".join(f"[{i['level']}] {i['code']}: {i['detail']}" for i in f["issues"]) or "—"
        last = f["last_run"]["status"] if f["last_run"] else "—"
        lines.append(f"| {f['task']} | {f['status']} | {last} | {f['consecutive_failures']} | "
                     f"{(f['last_success'] or '—')[:16]} | {issue} |")
    (LOGS / "HEALTH.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return 0 if report["overall"] == "OK" else (1 if report["overall"] == "WARNING" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
