#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.1 — 删除单个资产的封装
#
# 用法: ./remove-asset.sh <asset_id>
# 只能删除白名单中登记的 asset_id 对应文件；真正移除磁盘文件并留痕。
# =====================================================================
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "用法: $0 <asset_id>" >&2
    exit 2
fi

export PYTHONPATH=/opt/pool-poc
exec python3 -m pool --config /etc/pool-poc/config.yaml remove "$1"
