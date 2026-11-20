# Pool V0.2 公网部署说明

## 1. 当前公网拓扑（已部署并验证）

```
dl.bszx.site (DNSPod A → 156.233.227.20, TTL 300, DNS only)
     │
     ├─ :80   HTTP  ──  /.well-known/acme-challenge/ → /var/www/certbot-pool
     │                ──  其余全部 301 → https://
     │
     └─ :443  HTTPS ──  独立静态 vhost（root=/srv/pool/data，无反代）
                        GET/HEAD only · Range/ETag/Last-Modified 原生
                        404 兜底 · 无索引 · server_tokens off
                        nosniff + X-Robots-Tag noindex

127.0.0.1:8080  内部静态 vhost（V0.1 原样保留，仅回环，不公网暴露）
```

**方案选择**：公网 vhost 采用**独立静态服务**（与内部 8080 同 root），而非反代。
理由：零反代头处理成本，Range/206/ETag 原生无透传风险，改动面最小；
内部 8080 保留用于服务器本机验证与对比。

## 2. 文件清单（服务器）

| 文件 | 说明 |
| --- | --- |
| `/etc/nginx/sites-available/pool.conf` | 内部回环 vhost（127.0.0.1:8080）|
| `/etc/nginx/sites-available/pool-public.conf` | 公网 :80 + :443 vhost |
| `/etc/nginx/conf.d/pool-log.conf` | pool_traffic 日志格式 |
| `/etc/logrotate.d/pool-logs` | 日志轮转（每日 ×7 天）|
| `/etc/nginx/sites-available/default` | nginx 自带默认站（未动）|

## 3. 域名解耦（Phase 9 要求）

- 域名**只存在于两处**：nginx vhost 渲染参数（`POOL_DOMAIN` 环境变量）与文档；
- 资产 ID / relative_path / SHA256 / assets.yaml **不含任何域名**；
- 未来换域名：DNS 切换 + `POOL_DOMAIN=<新域名> deploy.sh` 重新签证书即可，
  **不重新下载、不改资产 ID、不改哈希、不改存储路径**。

## 4. 当前已知限制

- `dl.bszx.site` 是**临时测试域名**，不承诺长期可用；
- 未设 HSTS（待域名稳定后再评估）；
- 未启用 IPv6/AAAA（服务器 IPv6 未验证，避免不可达记录）；
- 服务器无防火墙，80/443/22 及其他所有端口均公网可达（历史现状）。
