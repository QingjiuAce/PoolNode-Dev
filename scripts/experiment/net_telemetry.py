#!/usr/bin/env python3
"""窗口分类用的链路遥测（PN-EXPERIMENT-01 §4.2）。

只做**读**操作，单个样本 < 2 KB：

* DNS 解析耗时
* TCP connect 耗时
* TLS 握手耗时（含协议版本 / ALPN）
* HTTP HEAD 的首字节时间（TTFB）
* 1 字节 Range GET（验证 206 + Content-Range 路径可用）
* ICMP 往返（origin 与本地上行对照目标），含丢包率

输出一个 JSON 文件（``--out``），并把摘要打到 stdout。
任一分项失败都如实记录，不影响其他分项，退出码仍为 0——
"测不到"本身是有效结论（窗口分类应为 UNKNOWN），不是异常。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit

DEFAULT_ORIGIN = os.environ.get("POOL_ORIGIN", "https://dl.bszx.site")
DEFAULT_PATH = "/pool-tests/bench-6m.jpg"
CONTROL_HOST = os.environ.get("POOL_TELEMETRY_CONTROL", "223.5.5.5")
PING_TIME_RE = re.compile(r"[=<]\s*(\d+)\s*ms")
LOSS_RE = re.compile(r"(\d+)%")


def _timed(fn):
    t0 = time.perf_counter()
    try:
        value = fn()
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 2), "value": value}
    except Exception as exc:  # noqa: BLE001 - 单分项失败即结论
        return {"ok": False, "ms": round((time.perf_counter() - t0) * 1000, 2),
                "error": f"{exc.__class__.__name__}: {exc}"[:200]}


def ping(host: str, count: int = 4) -> dict:
    if os.name == "nt":
        cmd = ["ping", "-n", str(count), "-w", "1000", host]
    else:
        cmd = ["ping", "-c", str(count), "-W", "1", host]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=count * 3 + 5)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:120]}
    text = (proc.stdout or "") + (proc.stderr or "")
    rtts = [int(m) for m in PING_TIME_RE.findall(text)]
    loss = None
    m = LOSS_RE.search(text)
    if m:
        loss = int(m.group(1))
    return {"ok": bool(rtts) or (proc.returncode == 0),
            "count": len(rtts),
            "min_ms": min(rtts) if rtts else None,
            "avg_ms": round(sum(rtts) / len(rtts), 1) if rtts else None,
            "max_ms": max(rtts) if rtts else None,
            "loss_pct": loss,
            "rc": proc.returncode}


def http_probe(origin: str, path: str, use_range: bool) -> dict:
    parts = urlsplit(origin)
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["http/1.1"])
    raw = socket.create_connection((host, port), timeout=10)
    try:
        sock = ctx.wrap_socket(raw, server_hostname=host) if parts.scheme == "https" else raw
        try:
            head = [f"GET {path} HTTP/1.1", f"Host: {host}",
                    "User-Agent: poolnode-telemetry/1", "Connection: close"]
            if use_range:
                head.append("Range: bytes=0-0")
            req = ("\r\n".join(head) + "\r\n\r\n").encode()
            sock.settimeout(15)
            t0 = time.perf_counter()
            sock.sendall(req)
            first = sock.recv(1)
            ttfb = round((time.perf_counter() - t0) * 1000, 2)
            data = first
            while len(data) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            head_text = data.split(b"\r\n\r\n", 1)[0].decode("latin-1", "replace")
            status = int(head_text.split()[1]) if head_text.startswith("HTTP/") else None
            body = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
            return {"ok": True, "status": status, "ttfb_ms": ttfb,
                    "tls": sock.version() if parts.scheme == "https" else None,
                    "alpn": sock.selected_alpn_protocol() if parts.scheme == "https" else None,
                    "body_bytes": len(body), "range": use_range}
        finally:
            sock.close()
    finally:
        raw.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PoolNode 链路遥测（只读）")
    ap.add_argument("--origin", default=DEFAULT_ORIGIN)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--control", default=CONTROL_HOST)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    host = urlsplit(args.origin).hostname
    now = dt.datetime.now().astimezone()
    rec = {
        "ts": now.isoformat(timespec="seconds"),
        "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "origin": args.origin,
        "path": args.path,
        "dns": _timed(lambda: socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)[0][4][0]),
        "tcp": _timed(lambda: socket.create_connection((host, 443), timeout=10).close()),
        "head": _timed(lambda: http_probe(args.origin, args.path, use_range=False)),
        "range1": _timed(lambda: http_probe(args.origin, args.path, use_range=True)),
        "ping_origin": ping(host),
        "ping_control": ping(args.control),
    }
    failed = [k for k in ("dns", "tcp", "head", "range1") if not rec[k]["ok"]]
    rec["summary"] = {"failed_items": failed,
                      "head_ttfb_ms": rec["head"].get("value", {}).get("ttfb_ms") if rec["head"]["ok"] else None,
                      "head_status": rec["head"].get("value", {}).get("status") if rec["head"]["ok"] else None,
                      "origin_loss_pct": rec["ping_origin"].get("loss_pct")}

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, out)
    print(json.dumps(rec["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
