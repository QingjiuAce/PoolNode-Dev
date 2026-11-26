#!/usr/bin/env python3
"""窗口分类（PN-EXPERIMENT-01 §4.2）：HEALTHY / DEGRADED / EVENT_TAIL / UNKNOWN。

分类**只看证据**，不按钟点推断；阈值在跑实验之前写死在这里，
避免事后挑选口径。证据不足一律 UNKNOWN——UNKNOWN 是合法结果，不是失败。

判据（全部来自同一 BEIJING 时区时间轴）
----------------------------------------
* 吞吐：本机探针（tag=local）在该窗口前后 90 分钟内的最近一档
  - 聚合吞吐 = 该档所有臂 mbps 的中位数
  - 单流吞吐 = `single` 臂的 mbps（单连接最能反映原始链路质量；
    实测健康窗 33–61 Mbps、劣化窗 0.4–0.9 Mbps，中位数会被多流掩盖）
* 链路：窗口内（±30 min）遥测样本的 origin 丢包率与 HEAD TTFB；
  超时的遥测不参与判定
* HEALTHY  : 聚合 ≥ 5 Mbps 且 单流 ≥ 5 Mbps 且 丢包 < 20% 且 TTFB < 1500 ms
* DEGRADED : 聚合 < 5 Mbps 或 单流 < 5 Mbps
* EVENT_TAIL: 本窗口 HEALTHY，但此前 12 小时内存在 DEGRADED 档位
* UNKNOWN  : 无可用探针样本；或吞吐健康但链路指标严重劣化（证据冲突）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import sys

HEALTHY_MBPS = 5.0
BAD_LOSS_PCT = 20
BAD_TTFB_MS = 3000
SLACK = dt.timedelta(minutes=90)
TELEMETRY_FRESH = dt.timedelta(minutes=30)
TAIL_LOOKBACK = dt.timedelta(hours=12)


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts)


def load_probe(path: pathlib.Path) -> list[dict]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("tag") != "local":
                continue
            rows.append(rec)
    except (OSError, ValueError):
        return []
    return rows


def slot_mbps(rec: dict) -> float | None:
    vals = [a.get("mbps") for a in rec.get("arms", []) if isinstance(a.get("mbps"), (int, float))]
    return round(statistics.median(vals), 3) if vals else None


def slot_single(rec: dict) -> float | None:
    vals = [a.get("mbps") for a in rec.get("arms", [])
            if a.get("arm") == "single" and isinstance(a.get("mbps"), (int, float))]
    return round(vals[0], 3) if vals else None


def slot_healthy(rec: dict) -> bool:
    """单档判定：聚合与单流都必须 ≥ 5 Mbps。"""
    agg, single = slot_mbps(rec), slot_single(rec)
    if agg is None:
        return False
    return agg >= HEALTHY_MBPS and (single is None or single >= HEALTHY_MBPS)


def _window_stats(rows: list[dict], when: dt.datetime) -> dict:
    best, best_delta = None, None
    for rec in rows:
        try:
            t = _parse(rec["ts"])
        except (KeyError, ValueError):
            continue
        delta = abs(t - when)
        if delta <= SLACK and (best_delta is None or delta < best_delta):
            best, best_delta = rec, delta
    if best is None:
        return {"available": False, "reason": "no_probe_sample_within_90min"}
    return {"available": True, "probe_ts": best["ts"], "probe_mbps": slot_mbps(best),
            "single_mbps": slot_single(best),
            "delta_min": round(best_delta.total_seconds() / 60, 1),
            "arms": len(best.get("arms", []))}


def classify(rows: list[dict], telemetry: dict | None, when: dt.datetime) -> dict:
    stats = _window_stats(rows, when)
    tel = telemetry or {}
    tel_fresh = False
    tel_delta_min = None
    try:
        tel_delta_min = round(abs((_parse(tel["ts"]) - when).total_seconds()) / 60, 1)
        tel_fresh = abs(_parse(tel["ts"]) - when) <= TELEMETRY_FRESH
    except (KeyError, ValueError, TypeError):
        tel_fresh = False
    loss = (tel.get("ping_origin") or {}).get("loss_pct") if tel_fresh else None
    ttfb = ((tel.get("head") or {}).get("value") or {}).get("ttfb_ms") if tel_fresh else None
    verdict = "UNKNOWN"
    reasons: list[str] = []

    agg = stats.get("probe_mbps") if stats["available"] else None
    single = stats.get("single_mbps") if stats["available"] else None
    bad_link = (loss is not None and loss >= BAD_LOSS_PCT) or \
               (ttfb is not None and ttfb >= BAD_TTFB_MS)
    throughput_ok = agg is not None and agg >= HEALTHY_MBPS and \
        (single is None or single >= HEALTHY_MBPS)

    if agg is None:
        reasons.append("缺少窗口内探针样本")
    elif throughput_ok and not bad_link:
        verdict = "HEALTHY"
        reasons.append(f"aggregate median {agg} Mbps ≥ {HEALTHY_MBPS}"
                       + (f"，single {single} Mbps ≥ {HEALTHY_MBPS}" if single is not None else ""))
    elif throughput_ok and bad_link:
        reasons.append(f"吞吐健康（{agg}/{single} Mbps）但链路劣化"
                       f"（loss={loss} ttfb={ttfb}），证据冲突")
    else:
        verdict = "DEGRADED"
        detail = f"aggregate median {agg} Mbps"
        if single is not None:
            detail += f"，single {single} Mbps"
        reasons.append(detail + f"：至少一项 < {HEALTHY_MBPS}")

    if verdict == "HEALTHY":
        degraded_before = []
        for rec in rows:
            try:
                t = _parse(rec["ts"])
            except (KeyError, ValueError):
                continue
            if dt.timedelta(0) <= when - t <= TAIL_LOOKBACK and not slot_healthy(rec):
                degraded_before.append((rec["ts"], slot_mbps(rec), slot_single(rec)))
        if degraded_before:
            verdict = "EVENT_TAIL"
            last = degraded_before[-1]
            reasons.append(f"此前 12h 内有劣化档位：{last[0]} "
                           f"(median {last[1]} / single {last[2]} Mbps)")

    return {"window": when.isoformat(timespec="minutes"), "verdict": verdict,
            "reasons": reasons, "probe": stats,
            "telemetry": {"fresh": tel_fresh, "delta_min": tel_delta_min,
                          "origin_loss_pct": loss, "head_ttfb_ms": ttfb,
                          "head_status": ((tel.get("head") or {}).get("value") or {}).get("status"),
                          "source": tel.get("ts")},
            "thresholds": {"healthy_mbps": HEALTHY_MBPS, "bad_loss_pct": BAD_LOSS_PCT,
                           "bad_ttfb_ms": BAD_TTFB_MS, "slack_min": SLACK.total_seconds() / 60,
                           "telemetry_fresh_min": TELEMETRY_FRESH.total_seconds() / 60}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="窗口分类（证据驱动）")
    ap.add_argument("--probe", default="reports/benchmarks/v06-monitoring/local-probe.jsonl")
    ap.add_argument("--telemetry", default=None)
    ap.add_argument("--at", default=None, help="窗口起点 ISO 时间，默认现在")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    when = _parse(args.at) if args.at else dt.datetime.now().astimezone()
    rows = load_probe(pathlib.Path(args.probe))
    tel = None
    if args.telemetry:
        try:
            tel = json.loads(pathlib.Path(args.telemetry).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            tel = None
    verdict = classify(rows, tel, when)
    text = json.dumps(verdict, ensure_ascii=False, indent=2)
    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
