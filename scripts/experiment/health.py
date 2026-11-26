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


def evaluate(now: dt.datetime) -> dict:
    findings = []
    for name, task in TASKS.items():
        states = load_states(name)
        script = ROOT / task["script"]
        entry = {"task": name, "runs": len(states), "status": "UNKNOWN",
                 "consecutive_failures": 0, "last_success": None, "last_run": None,
                 "issues": []}

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
                entry["issues"].append({"level": "CRITICAL", "code": "never_succeeded",
                                        "detail": "有运行记录但从未成功"})

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
