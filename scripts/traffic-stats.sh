#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.1 — 流量统计（在服务器上运行）
#
# 用法: ./traffic-stats.sh [月预算GB]
#   默认预算 1000 GB（1 TB）
#
# 数据来源: /var/log/nginx/pool-access.log（pool_traffic 格式，含 bytes=N）
# 注意:
#   * 该统计只反映 Pool 专属 vhost 的发送字节，是估算值，
#     不等于运营商结算流量（整台 VPS 所有端口都会计入结算）。
#   * 访问日志 logrotate 保留 7 天，因此“本月累计”依赖累计文件
#     /var/log/pool-poc/traffic-accumulator.tsv（建议 cron 每日执行本脚本）。
# =====================================================================
set -euo pipefail

BUDGET_GB="${1:-1000}"
LOG=/var/log/nginx/pool-access.log
ACC=/var/log/pool-poc/traffic-accumulator.tsv   # 每行: YYYY-MM-DD 当日字节
TODAY=$(date +%Y-%m-%d)
TODAY_NGX=$(date +'%d/%b/%Y')                   # nginx $time_local 日期前缀

[[ -f $LOG ]] || { echo "访问日志不存在: $LOG"; exit 0; }

# 今日发送字节：匹配 nginx 日期前缀，提取 bytes=N 字段
TODAY_BYTES=$(awk -v d="[$TODAY_NGX" \
    'index($0, d) { if (match($0, /bytes=[0-9]+/)) s += substr($0, RSTART+6, RLENGTH-6)+0 }
     END { print s+0 }' "$LOG")

# 状态码计数（提取 "] \"REQUEST code " 中的三位数字）
count_status() {
    awk -v want="$1" \
        'match($0, /" [0-9][0-9][0-9] /) { st = substr($0, RSTART+2, 3); if (st == want) c++ }
         END { print c+0 }' "$LOG"
}

# 累计文件维护：当天重复执行则原地更新（幂等）
mkdir -p "$(dirname "$ACC")" 2>/dev/null || true
if [[ -f $ACC ]] && grep -q "^$TODAY " "$ACC"; then
    sed -i "s|^$TODAY .*|$TODAY $TODAY_BYTES|" "$ACC"
else
    grep -v "^$TODAY " "$ACC" 2>/dev/null > "$ACC.tmp" || true
    echo "$TODAY $TODAY_BYTES" >> "$ACC.tmp"
    mv "$ACC.tmp" "$ACC"
fi

MONTH_PREFIX=$(date +%Y-%m)
MONTH_BYTES=$(awk -v m="$MONTH_PREFIX" 'index($1, m)==1 { s+=$2 } END { print s+0 }' "$ACC")
TOTAL_REQ=$(wc -l < "$LOG")

MB=$(( MONTH_BYTES / 1024 / 1024 ))
PCT=$(( MONTH_BYTES * 100 / (BUDGET_GB * 1024 * 1024 * 1024) ))

echo "===== Pool 流量统计 $(date '+%F %T %Z') ====="
echo "今日发送        : $(( TODAY_BYTES / 1024 )) KB ($TODAY_BYTES bytes)"
echo "本月累计        : ${MB} MB / ${BUDGET_GB} GB (${PCT}%)"
echo "当前日志内请求  : $TOTAL_REQ 条（logrotate 保留 7 天）"
echo "  HTTP 200      : $(count_status 200)"
echo "  HTTP 206      : $(count_status 206)"
echo "  HTTP 404      : $(count_status 404)"
echo
if   (( PCT >= 95 )); then echo "⚠️  流量严重警告: 本月已达预算 ${PCT}%（≥95%）"
elif (( PCT >= 85 )); then echo "⚠️  流量警告: 本月已达预算 ${PCT}%（≥85%）"
elif (( PCT >= 70 )); then echo "流量 INFO: 本月已达预算 ${PCT}%（≥70%）"
else                       echo "预算状态: 正常 (${PCT}%)"
fi

# ---- 磁盘使用率监测（数据盘预警: 60/75/85/90）----
echo
echo "===== 磁盘监测 ====="
check_disk() {
    local name="$1" path="$2" used
    used=$(df --output=pcent "$path" 2>/dev/null | tail -1 | tr -dc '0-9')
    case "$name" in
      数据盘)
        if   (( used >= 90 )); then echo "  $name: ${used}% ⚠️ 严重告警（≥90%）"
        elif (( used >= 85 )); then echo "  $name: ${used}% ⚠️ 达 85% —— 暂停非必要新增同步"
        elif (( used >= 75 )); then echo "  $name: ${used}% 警告（≥75%）"
        elif (( used >= 60 )); then echo "  $name: ${used}% 关注（≥60%）"
        else                        echo "  $name: ${used}% 正常"
        fi
        ;;
      *) echo "  $name: ${used}%" ;;
    esac
}
check_disk 系统盘 /
check_disk 数据盘 /srv/pool
echo
echo "提醒: 此为 Pool 日志估算，非运营商结算流量；结算以服务商控制台为准。"
