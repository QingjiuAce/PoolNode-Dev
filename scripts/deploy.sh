#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.1 — 部署脚本（在目标服务器上以 root 运行）
#
# 前置：install.sh 已执行；本项目代码已上传到 /opt/pool-poc/src。
#
# 行为：
#   1. 同步代码到 /opt/pool-poc/pool（备份旧版）
#   2. 安装配置到 /etc/pool-poc（config.yaml / assets.yaml）
#   3. 渲染并安装 Pool 专属 Nginx vhost（默认回环 127.0.0.1:8080）
#   4. nginx -t 通过后才 reload；失败自动回滚 vhost 文件
#
# 环境变量：
#   POOL_MODE=loopback|public   loopback=127.0.0.1:8080（默认，域名就绪前）
#                               public=:80 + server_name=$POOL_DOMAIN
#   POOL_DOMAIN=<域名>          public 模式必填
# =====================================================================
set -euo pipefail

SRC=/opt/pool-poc/src
DST=/opt/pool-poc/pool
MODE="${POOL_MODE:-loopback}"

echo "==> [1/4] 部署代码 -> $DST（旧版备份到 /opt/pool-poc/backup/）"
mkdir -p /opt/pool-poc/backup
if [[ -d $DST ]]; then
    tar -C /opt/pool-poc -czf "/opt/pool-poc/backup/pool-$(date +%Y%m%d%H%M%S).tgz" pool
fi
mkdir -p "$DST"
cp -r "$SRC"/pool/. "$DST"/
cp "$SRC"/requirements.txt "$DST"/ 2>/dev/null || true
find "$DST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
chown -R root:pool "$DST"
chmod -R 0644 "$DST"
find "$DST" -type d -exec chmod 0755 {} +

echo "==> [2/4] 安装配置 -> /etc/pool-poc"
for f in config assets; do
    if [[ -f "$SRC/config/$f.yaml" ]]; then
        install -o root -g pool -m 0640 "$SRC/config/$f.yaml" "/etc/pool-poc/$f.yaml"
    elif [[ -f "$SRC/config/$f.example.yaml" ]]; then
        install -o root -g pool -m 0640 "$SRC/config/$f.example.yaml" "/etc/pool-poc/$f.yaml"
        echo "    注意: 使用 example 配置作为生产配置（$f.yaml）"
    fi
done

echo "==> [3/4] 安装 Nginx 日志格式与 vhost（模式: $MODE）"
cat > /etc/nginx/conf.d/pool-log.conf <<'EOF'
# Pool 专属日志格式：$bytes_sent 用于流量统计，$request_time 用于性能分析
log_format pool_traffic '$remote_addr - [$time_local] "$request" '
                        '$status $body_bytes_sent bytes=$bytes_sent '
                        'rt=$request_time "$http_user_agent"';
EOF

sed -e "s|__POOL_LISTEN__|${POOL_LISTEN:-127.0.0.1:8080}|" \
    -e "s|__POOL_SERVER_NAME__|${POOL_SERVER_NAME:-_}|" \
    "$SRC/scripts/nginx-pool.conf.template" > /etc/nginx/sites-available/pool.conf

if [[ "$MODE" == "public" ]]; then
    if [[ -z "${POOL_DOMAIN:-}" ]]; then
        echo "错误: public 模式需要 POOL_DOMAIN" >&2
        exit 1
    fi
    sed -e "s|__POOL_LISTEN__|80|" \
        -e "s|__POOL_SERVER_NAME__|$POOL_DOMAIN|" \
        "$SRC/scripts/nginx-pool.conf.template" > /etc/nginx/sites-available/pool.conf
    echo "    公网 :80 vhost 已生成（HTTPS 待 certbot 签发后补 443）"
fi

chown root:root /etc/nginx/sites-available/pool.conf
ln -sfn /etc/nginx/sites-available/pool.conf /etc/nginx/sites-enabled/pool.conf

echo "==> [4/4] nginx -t 验证后 reload（失败自动回滚）"
if nginx -t; then
    systemctl reload nginx
    echo "DEPLOY DONE (mode=$MODE)"
else
    echo "nginx -t 失败，回滚 vhost" >&2
    rm -f /etc/nginx/sites-enabled/pool.conf
    nginx -t && systemctl reload nginx || true
    exit 1
fi
