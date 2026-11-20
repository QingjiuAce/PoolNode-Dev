# Pool V0.2 部署前预检报告（Stage 0）

> 时间：2026-11-21 06:54 CST（服务器）· 只读检查，未做任何变更

## 1. V0.1 基线状态（全部通过）

| 检查项 | 结果 |
| --- | --- |
| nginx 服务 | active / enabled，`nginx -t` 通过 |
| 内部服务 | `127.0.0.1:8080` 返回 200（未公网暴露 ✅）|
| 数据盘 | `/srv/pool → /dev/vdb1`（ext4，49G，1% 使用）findmnt 确认 |
| 生产数据 | `test-poc-001` verify PASS |
| 自动化测试 | 服务器 pytest **77 passed**（基线复跑）|
| 端口占用 | 22 (sshd)、80 (nginx stock default 站)、8080 (pool 回环)；**443 空闲** |
| vhost 清单 | `default`（nginx 自带）+ `pool.conf`（V0.1 回环），无其他业务 vhost |
| 磁盘/内存 | 系统盘 7% 使用；内存 334Mi / 3.8Gi |
| 现有业务影响 | 无 —— 机器上仅 sshd + nginx(pool) + 基础服务 |

## 2. 发现的问题（V0.2 需修复）

| # | 问题 | 影响 | 修复计划 |
| --- | --- | --- | --- |
| P1 | **logrotate 配置未部署**：V0.1 写了 `scripts/logrotate-pool.conf` 但 deploy.sh 未安装到 `/etc/logrotate.d/`，pool 日志目前不轮转 | 日志无限增长风险 | deploy.sh 增加 logrotate 安装步骤 |
| P2 | **数据盘误写保护缺失**：若 vdb1 掉挂载，`/srv/pool` 会落在系统盘，sync 会静默把文件写进系统盘 | 系统盘被填满风险 | config 增加 `guard_system_disk`（sync 时 st_dev 对比 `/`，同设备即拒绝）+ 测试 |
| P3 | **deploy.sh public 模式不完整**：仅写 :80 vhost，无 443/HTTPS 重定向/ACME webroot/证书条件装载 | 无法直接用于公网 | 新增公网 vhost 模板（http + https 两段），证书存在时才装 443 块 |

## 3. DNS 现状（Stage 1 阻塞项）

- `dl.bszx.site`：**NXDOMAIN**（Google DoH 权威应答，bszx.site 托管于 DNSPod：
  SOA `mirfac.dnspod.net`）——`dl` A 记录尚未创建。
- 无 AAAA 记录（正确：服务器未确认 IPv6 可用前不应添加）。
- 根域名与其他子域未做任何变更（遵守边界）。

**Certbot 签发被此阻塞**。需用户在 DNSPod 添加：

| 项 | 值 |
| --- | --- |
| 主机记录 | `dl` |
| 记录类型 | `A` |
| 记录值 | `156.233.227.20` |
| TTL | `300` |
| 线路 | 默认 |

DNSPod 本身无 CDN 代理概念，即天然 DNS Only。添加后等待生效即可继续 Stage 2/3。

## 4. 当前服务风险

- root 密码 SSH 公网开放 + 服务器无防火墙（历史现状，延续 V0.1 报告；
  80/443 公网开放靠的正是"无防火墙"，不新增规则）。
- nginx stock default 站点占 :80（公网可见 nginx 欢迎页）；公网 vhost 用
  `server_name dl.bszx.site` 精确匹配，不影响 default，不动它。
- certbot 未安装（待 DNS 就绪后安装）。

## 5. 结论

V0.1 状态健康，可以进入 V0.2。按"先完成不依赖 DNS 的工作"原则：
git 基线已提交 → 先修 P1/P2/P3 + 公网测试资产 + 大陆测速脚本，
DNS 生效后再执行 certbot 与公网验收。
