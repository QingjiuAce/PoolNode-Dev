# TLS 部署手册（V0.2）

> 域名：`dl.bszx.site` · 证书：Let's Encrypt（HTTP-01 · webroot）

## 1. 证书状态

| 项 | 值 |
| --- | --- |
| 主体 | CN=dl.bszx.site |
| 签发者 | Let's Encrypt (YE2) |
| 有效期 | 2026-11-20 22:05 UTC → **2026-02-18 22:05 UTC**（90 天）|
| 类型 | ECDSA |
| 位置 | `/etc/letsencrypt/live/dl.bszx.site/{fullchain,privkey}.pem` |
| 账户邮箱 | 3683326839@qq.com（可 `certbot update_account --email <新邮箱>` 更换）|

## 2. 签发流程（已完成，记录供复现）

```bash
# 0. 前提：DNS A 记录生效 + 公网 :80 vhost 已部署（ACME webroot 路径已接线）
POOL_MODE=public POOL_DOMAIN=dl.bszx.site bash scripts/deploy.sh

# 1. staging 演练（不消耗正式额度）
certbot certonly --webroot -w /var/www/certbot-pool -d dl.bszx.site \
    --staging --agree-tos -m 3683326839@qq.com -n
certbot delete --cert-name dl.bszx.site -n     # 清理 staging lineage

# 2. 正式签发
certbot certonly --webroot -w /var/www/certbot-pool -d dl.bszx.site \
    --agree-tos -m 3683326839@qq.com -n

# 3. 装载 443（deploy.sh 检测到证书后自动拼接 HTTPS 段）
POOL_MODE=public POOL_DOMAIN=dl.bszx.site bash scripts/deploy.sh
```

## 3. 自动续期（已验证）

- 机制：webroot authenticator 写入 renewal conf；`certbot.timer`（systemd）每日两次触发，
  仅在到期 ≤30 天时实际续期；续期后 hook 自动 reload nginx（Debian 包内置）。
- 验证：**`certbot renew --dry-run` → "Congratulations, all simulated renewals succeeded"**
  （注意：非交互模式自带最长 ~8 分钟随机延迟，命令"卡住"属正常，看日志确认：
  `/var/log/letsencrypt/letsencrypt.log`）。
- timer：`systemctl list-timers certbot.timer` → enabled。

## 4. TLS 配置

```nginx
ssl_certificate     /etc/letsencrypt/live/dl.bszx.site/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/dl.bszx.site/privkey.pem;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
ssl_session_cache shared:pool_ssl:10m;
ssl_session_timeout 1d;
```

实测：TLSv1.3 协商成功、链验证 `Verify return code: 0 (ok)`。
未启用 HSTS（按任务书：待 HTTPS 长期稳定后再评估）。

## 5. 常见运维

```bash
certbot certificates                  # 查看证书状态
certbot renew --dry-run               # 演练续期（有随机延迟，耐心等）
certbot update_account -m <新邮箱> -n  # 更换联系邮箱
tail -30 /var/log/letsencrypt/letsencrypt.log
```

## 6. 失败排查

- ACME 404/timeout：检查 :80 vhost 的 `/.well-known/acme-challenge/` 是否被
  重定向拦截（模板已用 `location ^~` 优先豁免）；
- DNS 未生效：`curl -s 'https://8.8.8.8/resolve?name=dl.bszx.site&type=A'`；
- 端口被封：`ss -lntp | grep :80` + 服务商安全组（当前无防火墙，公网直通）。
