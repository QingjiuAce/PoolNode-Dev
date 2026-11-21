#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.2 — 大陆网络直连测速（Linux/macOS/Git Bash）
#
# 用法:
#   bash china-speedtest.sh -u <下载URL> -i <运营商> -n <轮数>
#   运营商: unicom | telecom | mobile | broadcast | other（手动选择，勿按 IP 猜测）
# 示例:
#   bash china-speedtest.sh -u https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg -i mobile -n 3
#
# 输出:
#   reports/network-tests/<日期>/china-<运营商>.json   （机器可读）
#   reports/network-tests/<日期>/summary.md            （易读汇总，未测项=NOT TESTED）
#
# 直连要求: 检测到代理环境变量时中止（结果不能代表真实大陆路由）。
# 依赖: curl + sha256sum
# =====================================================================
set -uo pipefail

URL=""
ISP="other"
ROUNDS=3

while getopts "u:i:n:" opt; do
    case "$opt" in
        u) URL="$OPTARG" ;;
        i) ISP="$OPTARG" ;;
        n) ROUNDS="$OPTARG" ;;
        *) echo "用法: $0 -u <URL> -i <unicom|telecom|mobile|broadcast|other> [-n 轮数]" >&2; exit 2 ;;
    esac
done
[[ -z "$URL" ]] && { echo "用法: $0 -u <URL> -i <运营商> [-n 轮数]" >&2; exit 2; }

case "$ISP" in unicom|telecom|mobile|broadcast|other) ;; *) echo "非法运营商: $ISP" >&2; exit 2 ;; esac

HOST=$(echo "$URL" | sed -E 's|https?://([^/:]+).*|\1|')
STAMP=$(date '+%Y-%m-%dT%H:%M:%S%z')

echo "=== Pool 大陆直连测速 ==="
echo "URL  : $URL"
echo "ISP  : $ISP (手动选择)"
echo "时间 : $STAMP"

# 代理检测（直连要求）
PROXY_DETECTED=0
for v in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
    if [[ -n "${!v:-}" ]]; then
        echo "[中止] 检测到代理环境变量 $v=${!v} —— 本测试必须直连，请关闭代理后重跑。" >&2
        PROXY_DETECTED=1
    fi
done
[[ $PROXY_DETECTED -eq 1 ]] && exit 2
echo "[OK] 未检测到代理，直连测试"

# DNS
echo "--- DNS $HOST ---"
DNS_OK="false"; DNS_A="[]"
if command -v getent >/dev/null && getent ahostsv4 "$HOST" >/dev/null 2>&1; then
    DNS_OK="true"
    DNS_A=$(getent ahostsv4 "$HOST" | awk '{print $1}' | sort -u | awk '{printf \"%s\", $0 (NR>1?",":"") }' | sed 's/^/[/;s/$/]/')
    getent ahostsv4 "$HOST" | awk '{print "  A -> "$1}' | sort -u
else
    echo "  [WARN] DNS 解析失败"
fi

# ping（尽力而为）
PING_LINE="N/A"
if command -v ping >/dev/null; then
    PING_LINE=$( (ping -c 4 -W 3 "$HOST" 2>/dev/null || true) | tail -2 | head -1 )
    echo "--- ping ---"; echo "  $PING_LINE"
fi

# 下载轮次
W='code=%{http_code} dns=%{time_namelookup} tcp=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} speed=%{speed_download} size=%{size_download}'
ROUNDS_JSON=""
SPEEDS=""
for i in $(seq 1 "$ROUNDS"); do
    echo "--- 下载轮次 $i/$ROUNDS ---"
    TMP=$(mktemp)
    OUT=$(curl -sS -o "$TMP" -w "$W" --max-time 120 "$URL" 2>&1); RC=$?
    if [[ $RC -eq 0 && "$OUT" == *"code=200"* ]]; then
        SHA=$(sha256sum "$TMP" | awk '{print $1}')
        SPEED=$(echo "$OUT" | grep -o 'speed=[0-9.]*' | cut -d= -f2)
        CODE=$(echo "$OUT" | grep -o 'code=[0-9]*' | cut -d= -f2)
        STATUS="ok"
        [[ -n "$SPEED" ]] && SPEEDS="$SPEEDS $SPEED"
        echo "  HTTP $CODE  $(echo "$OUT" | grep -o 'size=[0-9]*')  sha256: $SHA"
    else
        SHA="null"; CODE="null"; STATUS="failed"
        echo "  [FAIL] rc=$RC $OUT"
    fi
    rm -f "$TMP"
    DETAIL=$(python3 - "$OUT" "$SHA" "$STATUS" "$CODE" <<'PYEOF'
import json, sys
kv = {}
for tok in sys.argv[1].split():
    if "=" in tok:
        k, v = tok.split("=", 1)
        try:
            kv[k] = float(v) if k != "code" else int(v)
        except ValueError:
            kv[k] = v
kv["sha256"] = sys.argv[2] if sys.argv[2] != "null" else None
kv["status"] = sys.argv[3]
kv["http_code"] = int(sys.argv[4]) if sys.argv[4] != "null" else None
print(json.dumps(kv, ensure_ascii=False))
PYEOF
)
    ROUNDS_JSON="$ROUNDS_JSON,$DETAIL"
done

# 报告目录
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DIR="$SCRIPT_DIR/../reports/network-tests/$(date '+%Y-%m-%d')"
mkdir -p "$DIR"
JSON_FILE="$DIR/china-$ISP.json"

python3 - "$JSON_FILE" "$ISP" "$URL" "$HOST" "$STAMP" "$DNS_OK" "$DNS_A" "$PING_LINE" "${ROUNDS_JSON:,}" <<'PYEOF'
import json, sys
path, isp, url, host, stamp, dns_ok, dns_a, ping_line, rounds = sys.argv[1:10]
try:
    rounds = json.loads("[" + rounds[1:] + "]")
except Exception:
    rounds = []
record = {
    "test_time": stamp,
    "isp": isp,
    "isp_source": "manually selected",
    "url": url,
    "host": host,
    "dns_ok": dns_ok == "true",
    "dns_a": json.loads(dns_a) if dns_a.startswith("[") else [],
    "ping": ping_line,
    "rounds": rounds,
    "notes": "direct connection; proxy checked before run",
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(record, f, ensure_ascii=False, indent=2)
print(f"[OK] JSON 报告: {path}")
PYEOF

# summary.md（四个运营商，未测 = NOT TESTED）
MD="$DIR/summary.md"
declare -A LABELS=([unicom]="中国联通" [telecom]="中国电信" [mobile]="中国移动" [broadcast]="中国广电")
{
    echo "# Pool 大陆网络测试汇总（$(date '+%Y-%m-%d')）"
    echo
    echo "| 运营商 | 测试时间 | 状态 |"
    echo "| --- | --- | --- |"
    for isp in unicom telecom mobile broadcast; do
        if [[ -f "$DIR/china-$isp.json" ]]; then
            echo "| ${LABELS[$isp]} | $(date '+%F %T') | TESTED（见 json） |"
        else
            echo "| ${LABELS[$isp]} | - | **NOT TESTED** |"
        fi
    done
    echo
    echo "_晚高峰（北京时间 20:00–23:00）测试以同目录其他文件为准。_"
} > "$MD"
echo "[OK] 汇总: $MD"
echo "完成。请把 JSON + summary.md 连同运营商/城市/接入方式一起反馈。"
