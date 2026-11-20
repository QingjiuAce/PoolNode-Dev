#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.1 — 服务器安装脚本（在目标服务器上以 root 运行）
#
# 只做：安装 nginx/python3-yaml/logrotate、创建专用用户与目录、
#       配置权限。不改动防火墙、不重启服务、不触碰 SSH。
#
# 数据盘挂载（/dev/vdb1 -> /srv/pool/data）由部署者手动执行或在
# 本脚本 --mount 模式下执行（要求 vdb1 为 ext4 且挂载点为空）。
# =====================================================================
set -euo pipefail

MOUNT=0
[[ "${1:-}" == "--mount" ]] && MOUNT=1

echo "==> [1/6] 安装系统包（nginx / python3-yaml / logrotate）"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx python3-yaml logrotate
nginx -v

echo "==> [2/6] 创建专用系统用户 pool（nologin，无 sudo）"
if ! id pool >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin pool
    echo "    用户 pool 已创建"
else
    echo "    用户 pool 已存在，跳过"
fi

echo "==> [3/6] 挂载 50G 数据盘到 /srv/pool（可选）"
if [[ $MOUNT -eq 1 ]]; then
    if mountpoint -q /srv/pool; then
        echo "    /srv/pool 已是挂载点，跳过"
    else
        FSTYPE=$(blkid -s TYPE -o value /dev/vdb1 || true)
        if [[ "$FSTYPE" != "ext4" ]]; then
            echo "    错误: /dev/vdb1 不是 ext4（实际: ${FSTYPE:-无文件系统}），拒绝自动挂载" >&2
            exit 1
        fi
        mkdir -p /srv/pool
        mount /dev/vdb1 /srv/pool
        if ! grep -q '63a07b43' /etc/fstab; then
            cp /etc/fstab /etc/fstab.pool-backup.$(date +%s)
            echo 'UUID=63a07b43-1d19-43d8-bbf7-0ffad641e24b  /srv/pool  ext4  defaults,nofail  0 2' >> /etc/fstab
            echo "    已挂载并写入 fstab（原 fstab 已备份）"
        fi
    fi
fi

echo "==> [4/6] 创建目录结构（全部为新增，不覆盖既有内容）"
mkdir -p /opt/pool-poc /etc/pool-poc
mkdir -p /srv/pool/data /srv/pool/tmp /srv/pool/var

echo "==> [5/6] 配置属主与权限（最小权限原则）"
# data/: pool 可写（sync），www-data/world 可读可穿越（内容本就是公网公开文件）
chown pool:pool  /srv/pool/data && chmod 0755 /srv/pool/data
chown pool:pool  /srv/pool/tmp && chmod 0700 /srv/pool/tmp
chown pool:pool  /srv/pool/var && chmod 0750 /srv/pool/var
chown root:pool  /etc/pool-poc   && chmod 0750 /etc/pool-poc

echo "==> [6/6] 状态确认"
df -h /srv/pool | tail -1
ls -la /srv/pool/
echo "INSTALL DONE"
