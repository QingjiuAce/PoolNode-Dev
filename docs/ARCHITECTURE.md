# Pool PoC V0.1 架构设计

> 面向蓝色大肥鱼.com 的独立备用原图下载节点
> 版本：V0.1 · 状态：设计定稿（Phase 0 产出）
> 本文档只描述 V0.1 实际实现的内容，未来扩展点单独标注。

---

## 1. 定位

Pool 是一条**独立于 GitHub Raw 的只读原图下载路径**：

- 只做：获准资产的保存、SHA256 校验、静态文件下载、手动同步/删除、操作与流量记录。
- 不做：不替换 GitHub Raw、不改原站代码与 URL、不做对象存储迁移、不做搜索站、
  不提供上传接口、不做通用反代、不承诺 SLA、不接管内容审核与元数据。

## 2. 环境审查结论（Phase 0，2026-11-21）

| 项目 | 实测结果 |
| --- | --- |
| 服务器 | 156.233.227.20 · 香港 · AS137899 I LAYER LIMITED |
| 系统 | Debian 12.0，内核 6.1.0-10-amd64，时区 Asia/Shanghai |
| CPU/内存 | 4 核 Xeon E5-2686 v4 / 3.8 GB（可用 3.5 GB）|
| 系统盘 | /dev/vda1 30G（已用 2.1G，余 28G）|
| **数据盘** | **/dev/vdb1 50G ext4 已预格式化、未挂载、内容为空（仅 lost+found）** |
| Web 服务 | **未安装 nginx，无任何 web 服务包，无 /etc/nginx，无现存 vhost** |
| 监听端口 | 仅 sshd :22（本会话中安装），无其他服务 |
| 防火墙 | iptables 空、nft 空、ufw 未安装 —— 当前无规则（公网全开放状态）|
| SSH | :22 · PermitRootLogin yes · PasswordAuthentication yes（安全隐患，见 §9）|
| Python | 3.11.2 · OpenSSL 3.0.20 · pip3/certbot/git 均未安装 |
| /opt /srv | 均为空目录，无占用冲突 |
| 已有服务影响 | 无 —— 机器上仅 sshd + cron + qemu-guest-agent 等基础服务 |

网络实测（见前期测速报告）：大陆方向 ping 49ms / 0% 丢包；直连下载 237/211 Mbps；
三网回程直连（电信 CN2+163、移动 CMI、联通 163→219.158）。

**与任务书差异**：任务书写"数据盘约 50 GB"，实测 30G 系统盘 + 50G 数据盘（vdb1，未挂载），
两者合计与"50 GB 数据盘"说法吻合。挂载操作将在部署前单独获得批准。

## 3. 总体架构

```
                  ┌────────────────────────────────────────────────┐
                  │  管理端（仅服务器本地/SSH，无公网管理接口）        │
                  │  pool-poc CLI (python3, 用户 pool)              │
                  └───────┬────────────────────────────────────────┘
                          │ check-upstream / sync / verify / remove
                          ▼
   GitHub Raw ──HTTPS──▶ 下载器 downloader.py
   (raw.githubusercontent.com     │ 流式下载 · 域名白名单 · TLS 验证 · 大小上限
    + objects.githubusercontent.) ▼
                          校验器 validator.py
                          │ magic bytes 格式检查 · SHA256 对比 expected_sha256
                          ▼
                          存储层 storage.py
                          │ /srv/pool/tmp/ 临时目录 → os.replace() 原子移动
                          ▼
              /srv/pool/data/<repo_relative_path>   ← 50G 数据盘 (vdb1)
                          │
                          ▼
                   Nginx 独立 vhost（只读静态服务）
                   GET/HEAD · Range · 404 兜底 · 无索引 · 无写方法
                          │
                          ▼
        https://<POOL_DOMAIN>/<repo_relative_path>  → 用户下载原图
```

组成：**Nginx（公网面）+ Python CLI（管理面）+ 文件系统（存储）+ JSON 白名单（授权）**。
无数据库、无队列、无后台。状态即文件，审计即日志。

## 4. 目录布局

### 4.1 项目仓库（本地开发，F:\鲸鲸\GitHub\开源二创\Pool）

```
pool-poc/
├── README.md                  # 使用说明
├── LICENSE                    # Pool 自身代码许可（MIT，仅代码与文档）
├── requirements.txt           # 仅 PyYAML
├── config/
│   ├── config.example.yaml    # 运行配置示例（不含真实凭据）
│   └── assets.example.yaml    # 资产白名单示例（全部 pending/测试资产）
├── pool/
│   ├── __init__.py
│   ├── config.py              # 配置加载与校验
│   ├── permissions.py         # 白名单与授权判定
│   ├── validator.py           # 路径校验 / 格式检查 / SHA256
│   ├── downloader.py          # 上游 HTTPS 下载（域名白名单+重定向管控）
│   ├── storage.py             # 临时目录/原子移动/删除/同步记录
│   └── cli.py                 # check-upstream / sync / verify / remove / stats
├── scripts/
│   ├── install.sh             # 服务器安装（apt nginx、用户、目录、权限）
│   ├── deploy.sh              # 部署代码与配置（备份既有文件）
│   ├── nginx-pool.conf        # Nginx vhost 模板（独立文件，不碰他站）
│   ├── smoke-test.sh          # HTTP 面验收脚本（GET/HEAD/Range/404/…）
│   ├── traffic-stats.sh       # 流量统计（读取 Pool 专属日志）
│   └── remove-asset.sh        # 删除单资产的封装
├── tests/
│   ├── test_permissions.py    # 授权判定
│   ├── test_paths.py          # 路径校验/穿越
│   ├── test_hash.py           # SHA256
│   ├── test_downloader.py     # 下载器（本地 HTTP 测试服务器）
│   └── test_format.py         # magic bytes 格式检查
└── docs/
    ├── ARCHITECTURE.md        # 本文档
    ├── DEPLOYMENT.md          # 部署与回滚手册
    ├── SECURITY.md            # 安全说明
    ├── CHINA-TEST.md          # 大陆测试指引 + 用户可执行脚本说明
    └── TEST_REPORT.md         # Phase 4 验收报告
```

### 4.2 服务器部署路径（全部为新增，不覆盖任何现有目录）

存储预算（站长确认）：系统盘 30G 承载系统/Nginx/程序/系统日志；数据盘 50G
承载原图、同步临时文件与同步记录，初期最多用约 40G，保留 10G 安全余量
（`min_free_space_mb: 10240`，低于余量 sync 直接拒绝）。

| 路径 | 用途 | 属主/权限 |
| --- | --- | --- |
| `/opt/pool-poc/` | 程序代码 | `root:pool`，代码 0644 |
| `/etc/pool-poc/` | 生产配置（config.yaml + assets.yaml）| `root:pool` 0640 |
| `/srv/pool/` | **50G 数据盘挂载点**（/dev/vdb1，ext4）| — |
| `/srv/pool/data/` | **对外文件根（nginx root）** | `pool:pool` 0755，文件 0444 |
| `/srv/pool/tmp/` | 下载临时区（数据盘，公开根之外）| `pool:pool` 0700 |
| `/srv/pool/var/` | 同步记录 JSONL（资产元数据，公开根之外）| `pool:pool` 0750 |
| `/var/log/nginx/pool-access.log` | Nginx 访问日志（Pool 专属，系统盘）| root，logrotate 7 天 |

服务用户 `pool`（系统用户，nologin，无 sudo）。Nginx worker 以 `www-data` 运行，
对 `/srv/pool/data` 仅有**读**权限；`pool` 用户对 data 目录有写权限（同步时），
生产文件以 0444 落盘，防止运行中被改写。

### 4.3 数据盘挂载方案（站长已批准）

```
mount /dev/vdb1 → /srv/pool
fstab 追加：UUID=63a07b43-…  /srv/pool  ext4  defaults,nofail  0 2
```

- 厂商已预格式化 ext4、盘为空（已用 debugfs 只读核实），**无需格式化，无数据风险**。
- 修改 fstab 前备份为 `/etc/fstab.pool-backup.<时间戳>`。
- `nofail` 保证数据盘异常时不阻塞开机。
- tmp 与 data 同盘，`os.replace()` 原子发布零拷贝。

## 5. 关键设计决策

### 5.1 URL 与路径映射：URL = 仓库相对路径

```
https://<POOL_DOMAIN>/<repo_relative_path>
      ⇕ 一比一映射
/srv/pool/data/<repo_relative_path>
```

`<repo_relative_path>` 就是图片在 `lmy414/ai-girl-stickers` 仓库里的相对路径，例如：

```
dist/submissions/originals/<角色>/<文件名>.png
owner-picks/blue-maid.png
blue-fish-originals/<文件名>.png
```

**理由**：任务书推荐 `/submissions/originals/<relative_path>`，但仓库真实结构中原图
分布在 `dist/submissions/originals/`、`owner-picks/`、`blue-fish-originals/` 三处。
采用仓库相对路径可以：
1. 严格保留"原始相对路径"（任务书 §0.2 第 3 条）；
2. 用户从 GitHub Raw 地址换到 Pool 地址时只需替换域名部分，映射零歧义；
3. 未来增量同步可以直接 diff 仓库树。

示例：`https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/blue-maid.png`
→ `https://<POOL_DOMAIN>/owner-picks/blue-maid.png`

### 5.2 上游域名白名单（重定向管控）

允许的上游域名（精确后缀匹配）：

| 域名 | 用途 |
| --- | --- |
| `raw.githubusercontent.com` | 原图直链 |
| `objects.githubusercontent.com` | Raw 对大文件 302 跳转的签名 CDN 落点 |

- 下载器跟随重定向时**逐跳校验**域名，跳出白名单立即中止。
- 仅允许 `https://`，证书校验强制开启（`ssl.CERT_REQUIRED`，系统 CA）。
- `assets.yaml` 中每个资产的 `source_url` 主机也必须落在白名单内。

### 5.3 无官方哈希时的处理

GitHub 对 raw 文件不提供 SHA256 元数据（Git 内部是 SHA-1，不能等价）。
因此 V0.1 的事实标准是：**由站长在白名单中登记 expected_sha256**（来源：本地仓库
`git hash-object` 换算或下载后计算后人工登记），流程上：

1. `assets.yaml` 提供 `expected_sha256` → sync 执行**官方校验**，不一致即拒绝发布；
2. 未提供时 → 下载后计算哈希入库，记录标记 `hash_source: local`，**明确不声称完成官方校验**，
   并在 sync 输出与同步记录中显著提示"该资产尚未登记官方 SHA256"。

### 5.4 授权白名单模型（assets.yaml）

```yaml
assets:
  - asset_id: test-poc-001                    # 唯一 ID，slug 格式
    source_url: https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/<path>
    relative_path: <path>                     # 仓库相对路径，也是对外 URL
    permission_status: approved               # pending | approved | denied
    permission_evidence: >-                   # 非空字符串才算过证据关
      站长书面确认（Issue #xxx / 邮件日期 / 会话记录摘要）
    attribution: "蓝色大肥鱼 (https://蓝色大肥鱼.com)"
    license: "图片权利归原作者；经站长明确许可经 Pool 备用分发"
    expected_sha256: "<64位hex 或 null>"
    revision: 1
```

同步放行的**全部**条件（缺一不可，无默认批准）：

1. `permission_status == approved`（pending/denied 一律拒绝 sync）
2. `permission_evidence` 非空
3. `source_url` 主机在上游白名单内
4. `relative_path` 通过严格路径校验（见 §6.2）
5. 文件大小 ≤ `max_file_size`（默认 20 MiB，config 可调）
6. 实际格式 ∈ {JPEG, PNG, GIF, WebP}（magic bytes 判定；SVG 明确拒绝）
7. SHA256 校验通过（或按 §5.3 走"仅本地哈希"降级记录）

`check-upstream` 对任何资产只做连通性诊断，**不落盘任何字节到公开目录**。

### 5.5 测试素材策略（版权合规）

Phase 1-3 全部使用**本地生成的测试图片**（Python 标准库 zlib/struct 生成的
合法 PNG 与一份小型 GIF/WebP/JPEG 字节样本），SHA256 自算自校。
真实原图（如 `owner-picks/blue-maid.png`）仅可做 `check-upstream` 连通性诊断，
**不下载、不保存、不发布**，等待站长明确批准后才进入 sync 流程。

## 6. 安全设计

### 6.1 威胁模型 → 对策

| # | 威胁 | 对策 |
| --- | --- | --- |
| 1 | SSRF / 开放代理 | CLI 只接受 asset_id，不接受 URL 参数；URL 只来自白名单文件 |
| 2 | 路径穿越 | relative_path 归一化后必须落在存储根内；拒绝 `..`、绝对路径、`\`、控制字符、前后导空白；Nginx 侧拒绝编码穿越并 404 |
| 3 | 任意文件读取 | Nginx root 固定 /srv/pool/data，`location` 无 alias 拼接，无 PHP/代理传递 |
| 4 | 任意文件写入 | 公网面只有 GET/HEAD；data 目录对 www-data 只读；pool 用户仅写 tmp 区与 data 的授权子路径 |
| 5 | 目录索引 | `autoindex off`；对目录路径返回 403/404 |
| 6 | 哈希污染 | 原子发布前 SHA256 必须匹配（或显式降级标记）；临时目录不在公开路径内 |
| 7 | 未授权文件发布 | pending/denied 拒绝 sync；无 whitelist 记录的路径在公网必然 404（文件不存在）|
| 8 | 恶意重定向 | 逐跳域名白名单校验 |
| 9 | 恶意文件 | magic bytes 实测格式，不信任 Content-Type/扩展名；SVG 不放行 |
| 10 | 流量滥用 | Pool 专属访问日志 + traffic-stats 统计 + 70/85/95% 阈值提醒 |
| 11 | 日志泄露 | 日志在 /var/log，nginx 禁止访问任何 `.log`；root 目录无索引 |
| 12 | TLS 降级 | 下载器强制证书验证；服务端 certbot Let's Encrypt（域名就绪后）|

### 6.2 路径校验规则（validator.py）

`relative_path` 必须：

- 仅含 `[A-Za-z0-9._/-]`，且不含 `..` 段、不以 `/` 开头、不含 `\`、不含空白与控制字符；
- 解析后（`posixpath.normpath`）仍以声明的路径开头且不逃出存储根（双层防御：
  字符级白名单 + 归一化后的前缀校验）；
- 展开后的真实物理路径必须位于 `/srv/pool/data` 之内（`os.path.realpath` 复核）。

### 6.3 账号与权限

- 专用系统用户 `pool`（nologin），CLI 以该用户运行；
- nginx worker `www-data` 对 data 只读；
- `/etc/pool-poc/*.yaml` 0640 root:pool；
- 服务器密码/SSH 密钥/Token 不进任何仓库与脚本（部署脚本从环境变量或交互读取）。

## 7. Nginx 设计（独立 vhost，零侵入）

- 配置文件：`/etc/nginx/sites-available/pool.conf` → sites-enabled 软链；
  不修改 default vhost 与其他站点文件，`nginx -t` 通过后才 reload。
- 行为矩阵：GET/HEAD 允许；PUT/POST/DELETE 等 → `444/405`；目录 → 403（关闭索引）；
  不存在 → 精确 404 JSON 无关、纯静态 404 页；路径穿越编码（`%2e%2e`）由 nginx
  默认合并行为 + 校验头处理并返回 404。
- 响应头：`X-Content-Type-Options: nosniff`、`Cache-Control: public, max-age=3600, no-transform`、
  保留 `ETag` / `Last-Modified` / `Accept-Ranges`；`server_tokens off`；
  MIME 按扩展名映射并强制 image/* 白名单类型，未知类型 `application/octet-stream`。
- Range：nginx 静态文件原生支持 206/Content-Range，V0.1 不做分片合并。
- 日志：Pool 专属 `access_log`（含 `$bytes_sent`、`$request_time`、`$status`），
  logrotate 每日轮转保留 7 天，日志目录不属于公开路径。
- 不对静态图片开 gzip/brotli（已是压缩格式，避免无谓 CPU 消耗）。

## 8. 流量统计（Phase 4 交付 traffic-stats.sh）

从 Pool 专属访问日志聚合：今日发送字节、本月发送字节、下载次数、200/206/404 计数。
预算 1 TB，阈值 70% / 85% / 95% 分级提醒（V0.1 在 stats 输出中给状态；不自动扩容，
不动运营商计费口径）。**文档明确：日志统计 ≠ 运营商结算流量。**

## 9. HTTPS 计划与诚实声明

- 正式 PoC 下载 URL 需要 `<POOL_DOMAIN>` + DNS A 记录 + certbot 签发 Let's Encrypt。
  **当前用户未提供域名 —— HTTPS 状态：待完成，不编造 URL。**
- 域名就绪前：nginx vhost 先以 HTTP 仅绑定 **127.0.0.1 回环**完成全部功能验证
  （GET/HEAD/Range/404/穿越/索引），外部不可访问，符合"先内网/本机测试"要求；
  如需公网演示测试图片，将单独征求用户批准，并明确标注为临时 HTTP。
- 可选：以自签名证书在回环上预演 TLS 配置（明确标注非正式证书，仅验证配置语法）。
- 顺带安全提醒：当前 root 密码登录公网开放，建议部署后改用密钥 + 强密码（另行征求同意）。

## 10. 回滚方案

| 步骤 | 操作 | 影响面 |
| --- | --- | --- |
| 1 | `rm /etc/nginx/sites-enabled/pool.conf && nginx -t && systemctl reload nginx` | Pool 独立下线，其他 vhost 不受影响 |
| 2 | `systemctl stop nginx`（如为 Pool 独占机器可选）| 全部站点停止（本机仅 Pool 时适用）|
| 3 | `umount /srv/pool/data` + 移除 fstab 追加行（恢复备份文件）| 数据保留在 vdb1，只读可再挂 |
| 4 | 删除 `/opt/pool-poc /srv/pool /etc/pool-poc /var/log/pool-poc`、用户 `pool` | Pool 自有数据，不触碰他物 |
| 5 | GitHub Raw / 原站 / 图片仓库 | **全程零改动**（Pool 不写上游）|

## 11. V0.1 明确不实现（未来扩展点）

- 全量/增量仓库同步、自动监听 webhook（P3 预留：白名单 revision 字段 + CLI 可重复执行）
- 紧急批量下架 API（当前 remove 为 SSH 手动执行）
- 管理后台、多节点、任务队列、对象存储
- 真实图片批量收录（等授权流跑通）
