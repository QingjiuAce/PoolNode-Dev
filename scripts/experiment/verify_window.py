#!/usr/bin/env python3
"""单窗口数据核验（PN-EXPERIMENT-01-RUN-02 阶段 E）。

把一个 run_id 的正式记录从 JSONL 中切出来，产出：

    raw_results/runs-<run_id>.jsonl   # 该窗口的原始记录（只读副本）
    experiment_manifest.json          # 矩阵 / 配对 / 种子 / 逐条字段
    integrity_report.json             # schema、重复、SHA、预算一致性
    budget_report.json                # 预算状态与记录口径对照

只读原始文件，不修改、不补造。
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RUNS = REPO / "reports" / "benchmarks" / "cmin-experiment" / "runs.jsonl"
REQUIRED = ("run_id", "seq", "seed", "rep", "cmin", "workers", "arm", "chunks",
            "chunk_bytes", "size", "wall_ms", "mbps", "sha_ok", "status")


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C_MIN 单窗口核验")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--budget-state", default=None)
    ap.add_argument("--telemetry", default=None)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    runs_path = pathlib.Path(args.runs)
    out = pathlib.Path(args.out_dir)
    (out / "raw_results").mkdir(parents=True, exist_ok=True)

    raw_lines = [l for l in runs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, bad = [], 0
    for line in raw_lines:
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if rec.get("run_id") == args.run_id:
            rows.append(rec)
    rows.sort(key=lambda r: r.get("seq", 0))

    slice_path = out / "raw_results" / f"runs-{args.run_id}.jsonl"
    slice_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                          encoding="utf-8")

    # --- 矩阵 / 配对 ------------------------------------------------------
    pairs: dict[tuple, set] = collections.defaultdict(set)
    for r in rows:
        pairs[(r.get("rep"), r.get("workers"))].add(r.get("cmin"))
    pairing_ok = all(len(v) == 2 for v in pairs.values())
    pair_seq_ok = True
    for i in range(0, len(rows) - 1, 2):
        a, b = rows[i], rows[i + 1]
        if (a.get("rep"), a.get("workers")) != (b.get("rep"), b.get("workers")) \
                or {a.get("cmin"), b.get("cmin")} != {98304, 131072}:
            pair_seq_ok = False
            break
    chunks_ok, chunk_detail = True, []
    for r in rows:
        size, cmin, workers = r.get("size"), r.get("cmin"), r.get("workers")
        if not all(isinstance(x, int) for x in (size, cmin, workers)):
            chunks_ok = False
            break
        expect = max(1, min(workers, size // cmin))
        chunk_detail.append({"arm": r.get("arm"), "cmin": cmin,
                             "requested_workers": workers,
                             "effective_chunks": r.get("chunks"),
                             "expected_chunks": expect})
        if r.get("chunks") != expect:
            chunks_ok = False

    # --- 完整性 -----------------------------------------------------------
    arm_ids = [r.get("arm_id") for r in rows]
    missing = [i for i, r in enumerate(rows) if any(k not in r for k in REQUIRED)]
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    err_rows = [r for r in rows if r.get("status") != "ok"]
    sha_fail = [r for r in ok_rows if r.get("sha_ok") is not True]
    dup_keys = len(rows) - len({(r.get("run_id"), r.get("seq")) for r in rows})
    bytes_ok = sum(int(r.get("size") or 0) for r in ok_rows)

    budget = None
    if args.budget_state and pathlib.Path(args.budget_state).is_file():
        budget = json.loads(pathlib.Path(args.budget_state).read_text(encoding="utf-8"))

    manifest = {
        "run_id": args.run_id,
        "source": str(runs_path),
        "source_sha256": _sha(runs_path),
        "slice": str(slice_path),
        "slice_sha256": _sha(slice_path),
        "records": len(rows),
        "reps": sorted({r.get("rep") for r in rows if r.get("rep") is not None}),
        "groups": sorted({(r.get("workers"), r.get("arm")) for r in rows}),
        "seeds": sorted({r.get("seed") for r in rows}),
        "pairs": len(rows) // 2,
        "pairs_by_group": {f"rep{k[0]}-w{k[1]}": sorted(v) for k, v in sorted(pairs.items())},
        "chunk_matrix": chunk_detail,
        "records_head": rows[:2],
    }
    integrity = {
        "run_id": args.run_id,
        "records": len(rows),
        "expected_records": 40,
        "records_ok": len(rows) == 40,
        "missing_fields": missing,
        "duplicate_keys": dup_keys,
        "arm_id_unique": len(set(arm_ids)) == len(arm_ids),
        "pairing_complete": pairing_ok,
        "pair_sequence_back_to_back": pair_seq_ok,
        "chunk_plan_matches_script": chunks_ok,
        "sha_ok_records": len(ok_rows),
        "sha_failures": sha_fail,
        "error_records": len(err_rows),
        "actual_download_bytes": bytes_ok,
        "jsonl_bad_lines_in_source": bad,
        "all_pass": bool(len(rows) == 40 and not missing and dup_keys == 0
                         and len(set(arm_ids)) == len(arm_ids) and pairing_ok
                         and pair_seq_ok and chunks_ok and not sha_fail),
    }
    budget_report = {
        "run_id": args.run_id,
        "declared_max_bytes": 120_000_000,
        "planned_bytes": 40 * 1_778_514,
        "actual_download_bytes": bytes_ok,
        "error_records": len(err_rows),
        "runtime": budget,
        "within_cap": (budget is None or int(budget.get("spent", 0)) <= 120_000_000),
        "note": "runtime.spent 是请求级预留置（含 1 字节探测与重试），>= 记录字节数",
    }
    telemetry = None
    if args.telemetry and pathlib.Path(args.telemetry).is_file():
        telemetry = json.loads(pathlib.Path(args.telemetry).read_text(encoding="utf-8"))

    for name, payload in (("experiment_manifest.json", manifest),
                          ("integrity_report.json", integrity),
                          ("budget_report.json", budget_report)):
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    if telemetry is not None:
        (out / "telemetry.json").write_text(
            json.dumps(telemetry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"run_id": args.run_id, "records": len(rows),
                      "all_pass": integrity["all_pass"],
                      "bytes": bytes_ok, "errors": len(err_rows),
                      "spent": (budget or {}).get("spent")}, ensure_ascii=False))
    return 0 if integrity["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
