# Pool V0.2 回滚手册

> 原则：只拆 Pool 自己的东西；保留内部 PoC；不影响其他 vhost/业务；不删数据（除非用户明确要求）。

## 回滚前状态快照（部署时已记录）

- vhost：`pool.conf`（8080 回环）+ `pool-public.conf`（80/443 公网）
- 证书：`/etc/letsencrypt/live/dl.bszx.site/`（webroot /var/www/certbot-pool）
- 端口：22 / 80 / 443 / 8080(回环)
- 数据：`/srv/pool/data/{pool-tests,submissions}/…`（vdb1 挂载于 /srv/pool）

## Level 1 —— 仅关闭公网入口（保留内部服务与数据）

```bash
rm /etc/nginx/sites-enabled/pool-public.conf
nginx -t && systemctl reload nginx
# 效果：dl.bszx.site 立即失效；127.0.0.1:8080 内部服务保留；数据与证书保留
```

## Level 2 —— 关闭公网 + 移除证书自动续期

```bash
rm /etc/nginx/sites-enabled/pool-public.conf
certbot delete --cert-name dl.bszx.site -n     # 删除 lineage（停止续期）
nginx -t && systemctl reload nginx
```

## Level 3 —— 完整拆除公网层（保留数据）

```bash
rm -f /etc/nginx/sites-enabled/pool-public.conf \
      /etc/nginx/sites-available/pool-public.conf \
      /etc/logrotate.d/pool-logs \
      /var/www/certbot-pool -r
certbot delete --cert-name dl.bszx.site -n
nginx -t && systemctl reload nginx
# /srv/pool 数据保留；内部 8080 保留
```

## Level 4 —— 完全移除 Pool（含数据，需用户明确要求）

```bash
# 1. 停止服务面
rm -f /etc/nginx/sites-enabled/pool.conf /etc/nginx/sites-enabled/pool-public.conf
nginx -t && systemctl reload nginx
# 2. 卸载数据盘（数据保留在 vdb1）
umount /srv/pool
sed -i '\|/srv/pool|d' /etc/fstab    # 或恢复 /etc/fstab.pool-backup.*
# 3. 删除 Pool 自有文件与用户
rm -rf /opt/pool-poc /srv/pool /etc/pool-poc
rm -rf /var/www/certbot-pool /etc/logrotate.d/pool-logs /etc/nginx/conf.d/pool-log.conf
userdel pool
# 4. 证书（如 Level 2 未执行）
certbot delete --cert-name dl.bszx.site -n
# 5. nginx 为本项目安装且无其他站点时才执行：
#    apt-get purge nginx && apt-get autoremove
```

## 不可作为回滚手段的操作

- ❌ 重装系统 / 删除整个 /etc/nginx（除非确认无其他业务且用户批准）
- ❌ 清空防火墙（本来就没有规则，无需也无法"清空"）
- ❌ 修改 SSH 配置（与 Pool 无关，不在回滚范围）

## 回滚后验证

```bash
ss -lntp                              # 80/443/8080 监听状态符合预期
curl -I http://dl.bszx.site/          # 应 DNS 解析但连接失败/超时（Level ≥2）
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/pool-tests/test-poc-001.png   # Level 1-3 应 200
```
