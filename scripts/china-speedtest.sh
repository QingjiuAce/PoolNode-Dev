#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.1 — 大陆网络直连测速脚本（在大陆的电脑上运行）
#
# 用法:
#   bash china-speedtest.sh <下载URL> [重复次数]
# 示例:
#   bash china-speedtest.sh https://<POOL_DOMAIN>/pool-tests/test-poc-001.png 3
#
# 依赖: curl + sha256sum（Linux/macOS 原生；Windows 用 Git Bash 或 WSL）
# 输出: DNS / TCP / TLS / TTFB / 下载耗时 / 平均速度 / SHA256 / 丢包
# 建议在网络空闲与北京时间 20:00-23:00 晚高峰各跑一次并保存输出。
# =====================================================================
set -uo pipefail

URL="${1:-}"
RUNS="${2:-1}"

if [[ -z "$URL" ]]; then
    echo "用法: $0 <下载URL> [重复次数]" >&2
    exit 2
fi

HOST=$(echo "$URL" | sed -E 's|https?://([^/]+)/.*|\1|')
OUTFILE="pool-speedtest-$(date +%Y%m%d-%H%M%S).txt"

report() { echo "$@" | tee -a "$OUTFILE"; }

report "=============================================================="
report "Pool 大陆直连测速  $(date '+%F %T %Z')"
report "目标: $URL"
report "运营商/地区: 请手动记录（联通/电信/移动/广电 + 城市）"
report "=============================================================="

# 基础连通：ping 丢包与延迟
if command -v ping >/dev/null; then
    report "--- ping ${HOST} x10 ---"
    if ping -c 10 -W 3 "$HOST" >> "$OUTFILE" 2>&1; then
        tail -2 /dev/null
    fi
    PING_SUMMARY=$( (ping -c 10 -W 3 "$HOST" 2>/dev/null || true) | tail -2 | head -1 )
    report "ping 汇总: $PING_SUMMARY"
fi

# DNS
report "--- DNS ---"
if command -v nslookup >/dev/null; then
    nslookup "$HOST" 2>&1 | tail -4 | while read -r l; do report "  $l"; done
elif command -v host >/dev/null; then
    host "$HOST" | while read -r l; do report "  $l"; done
fi

i=1
while (( i <= RUNS )); do
    report "--- 下载测试 $i/$RUNS ---"
    # -w 指标: dns/tcp/tls/ttfb/total/speed + 状态码
    TMP=$(mktemp)
    CURL_OUT=$(curl -sS -o "$TMP" -w \
        'dns=%{time_namelookup}s tcp=%{time_connect}s tls=%{time_appconnect}s ttfb=%{time_starttransfer}s total=%{time_total}s speed=%{speed_download}B/s size=%{size_download}B code=%{http_code}' \
        --max-time 120 "$URL" 2>&1)
    CURL_RC=$?
    report "curl: $CURL_OUT (rc=$CURL_RC)"
    if [[ $CURL_RC -eq 0 ]] && echo "$CURL_OUT" | grep -q 'code=200'; then
        SHA=$(sha256sum "$TMP" | awk '{print $1}')
        report "sha256: $SHA"
    else
        report "sha256: （下载失败，无哈希）"
    fi
    rm -f "$TMP"
    i=$((i+1))
done

report "=============================================================="
report "完整输出已保存: $OUTFILE （请连同本行上方内容一起反馈）"
report "=============================================================="
