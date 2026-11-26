#!/usr/bin/env python3
"""窗口结束后的机械采集（不依赖 Agent 在线）。

找到 2026-11-27 12 点的正式 cmin 运行，切出原始记录、生成 manifest /
integrity / budget 报告并做窗口分类。只读数据、只写报告目录，不改代码、
不碰 git、不做任何 SSH 操作。

用法：
  python scripts/experiment/postrun_capture.py --date 2026-11-27 --hour 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
LOGS = REPO / "reports" / "experiment-logs"
OUT = REPO / "reports" / "experiments" / "PN-EXPERIMENT-01" / "cmin-first-window"


def find_run(date: str, hour: int) -> dict | None:
    best = None
    for p in (LOGS / "state" / "cmin").glob("*.json"):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        start = str(st.get("start_time") or "")
        if st.get("smoke") or not start.startswith(f"{date}T{hour:02d}:"):
            continue
        if best is None or start > str(best.get("start_time")):
            best = st
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--hour", type=int, default=12)
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    st = find_run(args.date, args.hour)
    capture = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "expected_window": f"{args.date}T{args.hour:02d}:15",
               "run_found": bool(st)}
    if not st:
        (OUT / "postrun_capture.json").write_text(
            json.dumps(capture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(capture, ensure_ascii=False))
        return 0

    rid = st.get("run_id")
    capture.update({"run_id": rid, "status": st.get("status"),
                    "exit_code": st.get("exit_code"),
                    "error_summary": st.get("error_summary"),
                    "record_count": st.get("record_count"),
                    "upload_status": st.get("upload_status"),
                    "budget_runtime": st.get("budget_runtime"),
                    "budget": st.get("budget"),
                    "auto_disable": st.get("auto_disable"),
                    "locks": st.get("locks")})

    py = sys.executable
    verify = subprocess.run(
        [py, "-X", "utf8", str(REPO / "scripts" / "experiment" / "verify_window.py"),
         "--run-id", str(rid), "--out-dir", str(OUT),
         "--budget-state", str(LOGS / "budget" / f"{rid}.json"),
         "--telemetry", str(LOGS / "telemetry" / f"{rid}.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    capture["verify_rc"] = verify.returncode
    capture["verify_out"] = (verify.stdout or "").strip()[-400:]

    classify = subprocess.run(
        [py, "-X", "utf8", str(REPO / "scripts" / "experiment" / "window_class.py"),
         "--telemetry", str(LOGS / "telemetry" / f"{rid}.json"),
         "--at", f"{args.date}T{args.hour:02d}:15:00+08:00",
         "--out", str(OUT / "analysis" / "classification.json")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    capture["classify_rc"] = classify.returncode

    (OUT / "postrun_capture.json").write_text(
        json.dumps(capture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in capture.items()
                      if k not in ("verify_out", "budget_runtime")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
