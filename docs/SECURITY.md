# Pool PoC V0.1 安全说明

## 1. 威胁模型与对策（已实现）

| # | 风险 | 对策 | 实现位置 |
| --- | --- | --- | --- |
| 1 | SSRF / 开放代理 | CLI 只接受 asset_id，URL 只能来自白名单文件；不接受运行时 URL 输入 | `cli.py` `permissions.py` |
| 2 | 上游域名欺骗/重定向逃逸 | 域名精确匹配白名单（`raw.githubusercontent.com`、`objects.githubusercontent.com`）；重定向逐跳校验，跳出即中止 | `downloader.py` `_url_allowed` `_SafeRedirectHandler` |
| 3 | 路径穿越 | 字符白名单正则 + `normpath` 恒等校验 + `realpath` 落根复核，三层防御；Nginx `--path-as-is` 穿越测试见 smoke-test | `validator.py` |
| 4 | 任意文件读取 | Nginx root 固定 `/srv/pool/data`，无 alias 拼接、无代理传递、无索引 | `nginx-pool.conf.template` |
| 5 | 任意文件写入 | 公网仅 GET/HEAD（`limit_except` deny）；www-data 对数据目录只读；pool 用户只写隔离 tmp 区 | vhost + `install.sh` 权限矩阵 |
| 6 | 目录索引 | autoindex 关闭；`location = /` 直接 404；目录 403 | vhost |
| 7 | 哈希污染 | 发布前 SHA256 必须与白名单官方登记值一致；不一致拒绝发布并清理临时文件 | `cli.py` `cmd_sync` |
| 8 | 未授权资产发布 | pending/denied/无证据一律拒绝；无默认批准；白名单外 asset_id 直接拒绝 | `permissions.py` |
| 9 | 恶意重定向 | 同 #2；`Location` 跳出白名单返回 `redirect_blocked` | `downloader.py` |
| 10 | 恶意文件/伪装 | magic bytes 实测格式（不信任扩展名与 Content-Type）；SVG 显式拒绝（脚本注入面） | `validator.py` |
| 11 | 大小滥用 | Content-Length 预检 + 流式累计上限（默认 20 MiB，可配置），超限立即中止并清理 | `downloader.py` |
| 12 | 流量滥用 | Pool 专属访问日志 + `traffic-stats.sh` 统计 + 70/85/95% 阈值分级 | `traffic-stats.sh` |
| 13 | 日志泄露 | 日志在 `/var/log`，公网 404 兜底 `\.(log|jsonl|yaml)$` 与隐藏文件 | vhost |
| 14 | TLS 降级 | 下载器强制系统 CA 证书校验（无任何跳过开关）；TLS 失败单独分类报告 | `downloader.py` |
| 15 | 不完整文件公开 | 下载在 `/srv/pool/tmp`（公开路径外），发布 = 校验后 `os.replace()` 原子移动；失败即清理 | `storage.py` |

## 2. 权限矩阵

| 路径 | 属主 | 权限 | 说明 |
| --- | --- | --- | --- |
| `/srv/pool/data` | pool:pool | 0755 | 公开根；pool 写（经 CLI），www-data/world 读（内容即公网文件）|
| 已发布文件 | pool | 0444 | 运行期只读，防改写 |
| `/srv/pool/tmp` | pool:pool | 0700 | 下载临时区，公开路径之外 |
| `/etc/pool-poc/*` | root:pool | 0640 | 白名单与配置 |
| `/var/log/pool-poc` | pool:pool | 0750 | 审计记录 |
| `/opt/pool-poc` | root:pool | 0644/0755 | 代码 |

服务用户 `pool`：系统用户、nologin、无 sudo。Nginx worker 以 www-data 运行。

## 3. 凭据管理

- 本仓库与所有脚本**不含**任何服务器密码、SSH 私钥、Token；
- SSH 使用交互式密码/密钥，部署脚本不落盘凭据；
- 生产配置（config.yaml/assets.yaml）不进 Git（.gitignore 已排除，只提交 example）。

## 4. 已知限制与残余风险（诚实清单）

1. **SSH 面暴露**：服务器当前 `PermitRootLogin yes + PasswordAuthentication yes`
   面向公网 22 端口。这是历史现状而非本项目引入；建议尽快改密钥登录 + 强密码
   （需要用户另行批准，Pool 部署不触碰 SSH 配置）。
2. **无防火墙**：服务器当前 iptables/nft 全空，所有端口公网可达。
   Pool 本身只新增 80/443（或回环 8080）监听；是否收紧整体防火墙由用户决策。
3. **HTTP 明文阶段**：域名就绪前回环模式无加密问题；公网模式在 certbot 之前
   理论上会明文（V0.1 计划直接等证书就绪再开公网，避免明文阶段）。
4. **expected_sha256 依赖人工登记**：哈希来源的正确性由站长保证（从 GitHub
   仓库核对）。CLI 在未登记时降级为本地哈希并明确标注，不冒充官方校验。
5. **流量统计 ≠ 计费流量**：日志统计只覆盖 Pool vhost 的发送字节；
   运营商按整机流量结算，可能包含 SSH、系统更新等其他流量。
6. **20 MiB 上限**：超过 20 MiB 的原图会被拒绝（CONTRIBUTING 建议 ≤10 MB，
   当前上限留有余量）。调整需改配置并知会。

## 5. 审计

- 每次同步/删除（含失败）追加 JSONL 记录：
  `/var/log/pool-poc/sync-records.jsonl`（时间戳、资产、哈希来源、结果、原因）；
- Nginx 侧每次下载记录：时间、路径、状态码、发送字节、耗时（保留 7 天）；
- 删除是物理删除（文件真正移除），对外即刻 404。
