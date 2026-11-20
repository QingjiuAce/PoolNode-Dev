# Pool PoC V0.1 测试验收报告

> 日期：2026-11-21 · 测试人：Pool 开发会话（自动化测试 + 实机验证）
> 服务器：156.233.227.20（香港 · AS137899 · Debian 12 · nginx 1.22.1）

## 1. 结论总览

| 项目 | 状态 |
| --- | --- |
| 核心链路（授权→下载→SHA256→原子发布→静态服务→外部下载）| ✅ 全链路验证通过 |
| SHA256 三方闭环（源 = Pool = 外部下载）| ✅ `a448e2bc…24f6` 三方一致 |
| HTTP 面（GET/HEAD/Range/404/穿越/方法/头）| ✅ 冒烟 23/23 |
| 授权约束（pending/denied/无证据拒绝）| ✅ 真实 pending 原图被拒（零字节落盘）|
| 自动化测试 | ✅ 本地 76 通过 / 服务器 77 通过 |
| HTTPS 正式 PoC URL | ⏳ **待用户提供域名**（诚实声明：当前无公网下载地址，未伪造）|
| 真实原图 PoC | ⏳ **等待站长授权**（已验证 pending 拒绝路径 + 上游连通性）|
| 大陆实测 | ⏳ 脚本就绪，等待外部大陆网络执行（含晚高峰）|

## 2. 实际部署状态

- 代码：`/opt/pool-poc/pool`（服务器 31+1 文件），生产配置 `/etc/pool-poc/{config,assets}.yaml`
- 数据盘：/dev/vdb1 50G ext4 → `/srv/pool`（fstab 已备份后追加 `nofail` 条目）
- 目录：`/srv/pool/{data,tmp,var}`，权限矩阵见 docs/SECURITY.md §2
- Nginx：独立 vhost `sites-available/pool.conf`，**仅监听 127.0.0.1:8080（回环）**；
  nginx 自带 default 站点（:80）未动 —— 如需关闭：`rm /etc/nginx/sites-enabled/default`
- 服务用户：`pool`（nologin，无 sudo）；nginx worker www-data 只读 data/
- 日志：`/var/log/nginx/pool-access.log`（pool_traffic 格式）+ `/srv/pool/var/sync-records.jsonl`
- logrotate：`/etc/logrotate.d/pool-logs`（每日，保留 7 天）

## 3. 测试命令与结果

### 3.1 自动化测试

| 环境 | 命令 | 结果 |
| --- | --- | --- |
| 本地 Windows / Python 3.14 | `python -m pytest tests/` | **76 passed, 1 skipped**（symlink 测试需 Linux）|
| 服务器 Debian 12 / Python 3.11 | `python3 -m pytest tests/` | **77 passed**（含 symlink 逃逸测试）|

覆盖：授权判定（pending/denied/无证据/未知资产/URL 白名单/子域欺骗）、
路径穿越（18 组非法路径 + symlink 逃逸）、SHA256、格式检查（PNG/JPEG/GIF/WebP/
伪装 SVG/HTML/空文件）、下载器（404 分类/超限中止/重定向管控/允许重定向）、
端到端 sync/verify/remove/篡改检测。

### 3.2 端到端 PoC（测试图片）

```
上游: 本地生成确定性 PNG（11026 bytes）
     sha256 = a448e2bc3027c71fa52c48c5b6c24e8f2ffc0709fd4ecfe6a283dd41e60424f6
  ↓ pool-poc sync test-poc-001（以 pool 用户运行）
[1/8] 授权检查 OK → [2/8] URL 校验 OK → [3/8] 路径+磁盘余量 OK（剩余 47512 MB）
     → [4/8] 流式下载 11026B → [5/8] 大小 OK → [6/8] 格式 png
     → [7/8] SHA256（本地基准，白名单未登记官方值，已明确标注降级）
     → [8/8] 原子发布 /srv/pool/data/pool-tests/test-poc-001.png
  ↓ pool-poc verify → PASS
```

### 3.3 HTTP 冒烟测试（smoke-test.sh，23/23 PASS）

GET 200 + Content-Type image/png + Content-Length；两次下载哈希一致；
HEAD 200；Accept-Ranges/ETag/Last-Modified；Range 0-1023 → **206 + Content-Range:
bytes 0-1023/11026 + 实收 1024 字节**；越界 Range → 416；不存在 → 404；
目录/根路径无索引；`--path-as-is` 穿越 → 400/404；POST/PUT/DELETE → 403；
隐藏文件 → 404；nosniff；Server 头无版本号。

### 3.4 外部客户端下载（SSH 隧道，大陆宽带 → 香港 → 回环 vhost）

| 项 | 结果 |
| --- | --- |
| HEAD | 200，头完整 |
| GET ×3 | 均 200，11026 B，~0.10 s/次（含 SSH 加密开销）|
| SHA256 | 三次均为 `a448e2bc…24f6` —— **与源、与 Pool 存储一致** |
| Range | 206 + Content-Range 正确 + 1024 B |

**诚实声明**：这是经 SSH 加密隧道访问回环 vhost，验证的是完整 HTTP 服务与
真实互联网传输路径；**不是**正式公网 HTTPS URL（域名未就绪，未伪造证书）。

### 3.5 上游回源诊断（check-upstream ×3，HK → GitHub Raw）

对象：`owner-picks/blue-maid.png`（**pending 资产，仅诊断，未保存任何字节**）

| 次 | DNS | TCP | TLS | HEAD | TTFB | 采样速度 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | OK 4.9ms | 1.5ms | TLS1.3 77ms | 200 image/png 1,384,473B | 12.0ms | 20.7 MB/s |
| 2 | OK 4.7ms | 2.0ms | TLS1.3 67ms | 200 | 10.2ms | 24.3 MB/s |
| 3 | OK 4.0ms | 1.9ms | TLS1.3 65ms | 200 | 10.1ms | 24.4 MB/s |

结论：香港节点回源 GitHub Raw 极佳（文件实际 1.38 MB）。按授权约束，
**未做该真实原图的全量下载与 SHA256 比对**（待批准后执行）。

### 3.6 授权拒绝验证

```
$ pool-poc sync example-real-pending
FAIL: 资产 example-real-pending 状态为 pending：尚未获得明确第三方托管许可，拒绝同步  (exit 1)
```
（蓝 fish 原图 `blue-fish-originals/026e44f4…` 为 denied 示例同样拒绝；
审计记录 `/srv/pool/var/sync-records.jsonl` 已记录 rejected/STATUS_PENDING）

### 3.7 流量统计

```
今日发送 206,499 bytes；HTTP 200 ×19 / 206 ×3 / 404 ×10；预算 0% 正常
```
（log_traffic 格式 + 累计文件机制已验证；logrotate 配置已安装）

## 4. 测试过程中发现并解决的问题

| # | 问题 | 修复 |
| --- | --- | --- |
| 1 | nginx `limit_except` 不允许在 server 块使用（nginx -t 失败）| 移入 `location /` 块；deploy.sh 失败自动回滚按设计生效 |
| 2 | Server 头泄露版本 `nginx/1.22.1` | 模板补 `server_tokens off` → `Server: nginx` |
| 3 | traffic-stats 日期匹配用错格式（`%Y-%m-%d` vs nginx `%d/%b/%Y`）+ 字段错位 | 改用 nginx 日期前缀 + `bytes=N` 正则提取 + 状态码三段匹配 |
| 4 | `data/` 原 0750 root:pool 导致 pool 不可写、www-data 不可穿越 | 改 `pool:pool 0755`（内容本为公网公开文件），文件仍 0444 |
| 5 | Windows 只读文件（0444）不可 `os.replace`/`unlink` | 发布前/删除前先摘只读位（POSIX 无影响）|
| 6 | paramiko `stdout.read()` 偶发空输出 | 全部改用 channel 手动循环读取 |
| 7 | URL 路径必须以 relative_path 结尾的校验曾与测试夹具不一致 | 修正夹具（该校验本身按设计工作）|

## 5. 尚未完成 / 待外部条件

1. **HTTPS 正式 PoC URL**：等用户提供 `<POOL_DOMAIN>` + DNS A 记录 → certbot
   签发 → `POOL_MODE=public POOL_DOMAIN=… deploy.sh` → 外部 `curl -fSLO` 验证。
   在此之前**不存在任何正式 Pool 下载 URL**。
2. **真实原图 PoC**：等站长对具体资产批准（approved + evidence + expected_sha256
   登记进 assets.yaml）→ `sync` → 全量 SHA256 三方闭环。当前只验证了 pending 拒绝
   与上游连通性，**未复制任何真实作品**。
3. **大陆多运营商实测（含晚高峰 20:00–23:00）**：脚本 `scripts/china-speedtest.sh`
   已就绪，等待大陆各网络环境执行并回传 `pool-speedtest-*.txt`。
4. **流量累计**：建议 cron 每日 06:00 跑 `traffic-stats.sh`（月度累计依赖每日入账）。

## 6. 已知安全风险（延续 SECURITY.md §4）

- **root 密码 SSH 公网开放**（历史现状）：强烈建议尽快改密钥登录/强密码；
- **服务器无防火墙**：iptables/nft 全空，全部端口公网可达；Pool 仅新增回环 8080；
- nginx 自带 default 站点在 :80 公网显示欢迎页（stock 配置，非 Pool 引入）；
- expected_sha256 依赖站长人工登记的准确性（未登记时 CLI 显式降级为本地哈希）。

## 7. 验收清单核对（任务书 §11）

**A. 数据完整性**：授权检查 ✅ / 测试文件下载 ✅ / 格式检查 ✅ / SHA256 ✅ /
字节数一致 ✅ / 正式目录无不完整文件 ✅（临时区隔离 + 失败清理）/ 原始文件名保留 ✅

**B. HTTP 服务**：GET 200 ✅ / HEAD 200 ✅ / Range 206 ✅ / Content-Range ✅ /
404 ✅ / 目录列表关闭 ✅ / 非法方法拒绝 ✅ / 直接下载 ✅ / HTTPS 证书 — ⏳ 域名待定

**C. 安全**：pending 无法同步 ✅ / denied 无法同步 ✅ / 任意 URL 代理不可用 ✅ /
路径穿越 ✅ / 未授权文件不可读 ✅（不存在即 404）/ 无公网上传 ✅ /
删除不影响他文件 ✅（测试覆盖）/ 无硬编码凭据 ✅

**D. 网络**：HK→Raw 真实回源 ✅ / 下载三次可复现 ✅ / Pool 对外下载 ✅（SSH 隧道）/
Range 完整 ✅ / 并发读取 ✅（ThreadingHTTPServer + nginx 并发；外部并发压测待公网）/
大陆端脚本就绪 ✅（实测 ⏳）
