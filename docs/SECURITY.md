# PoolNode 安全说明（V0.2 服务端）

> 适用代码：`main` 分支 · 服务端 V0.2
> 更新：2026-11-26
> 配套审计记录：[docs/SECURITY_AUDIT_V02.md](SECURITY_AUDIT_V02.md)

本文件面向部署者、审计者与希望了解边界的使用者，说明 PoolNode **实际实现了什么、
没有实现什么**。不把计划中的加固写成已交付能力；已知残余风险逐项列出。

---

## 1. 设计原则

1. **只读**：公网侧只有 `GET` / `HEAD`，没有上传、代理、动态接口或写入路径。
2. **授权先行**：任何资产在产生网络请求或临时文件之前，必须先通过白名单与授权检查。
3. **不信任上游**：URL 只能来自维护者清单；主机精确匹配；证书校验不可关闭；重定向逐跳复核。
4. **按内容校验**：格式看 magic bytes，完整性看 SHA256，大小有硬上限。
5. **不在半成品上对外服务**：下载先落公开目录之外的暂存区，校验通过后原子发布。
6. **可撤销**：删除是物理删除并留审计记录；对外路径立即 404。

---

## 2. 信任边界

```text
不可信输入
  ├─ 公网 HTTP 请求（仅 GET/HEAD）
  ├─ 上游图片响应（可能重定向、超时、短体、错误类型）
  └─ 白名单文件内容（由维护者签发，仍需逐项校验）

可信锚点
  ├─ 维护者签发的 assets.yaml（asset_id → 路径 + 授权 + expected_sha256）
  ├─ 服务器本地配置 /etc/pool-poc/config.yaml（root:pool 0640）
  └─ TLS 系统 CA 信任库

不可跨越的边界
  ├─ 公开根 /srv/pool/data 之外的文件不对外提供
  ├─ CLI 不接受运行时任意 URL
  └─ 公网不提供任何写入面
```

PoolNode 不试图防御“服务器 root 已被攻陷”或“维护者清单本身被恶意篡改”的场景；
这两类属于部署与供应链信任边界，需要独立的密钥、权限与审计措施。

---

## 3. 威胁模型与已实现对策

| # | 风险 | 对策 | 实现位置 |
|---|---|---|---|
| 1 | SSRF / 开放代理 | CLI 只接受 `asset_id`；URL 只能来自白名单；不接受运行时 URL | `pool/cli.py`、`pool/permissions.py` |
| 2 | 上游域名欺骗 | 主机名精确匹配白名单，不做后缀通配 | `pool/downloader.py` `_url_allowed` |
| 3 | 重定向逃逸 | 每一跳重新校验 scheme/主机；跳出白名单立即中止 | `pool/downloader.py` `_SafeRedirectHandler` |
| 4 | 路径穿越 | 字符白名单 + `normpath` 恒等 + `realpath` 落根复核；nginx `try_files` | `pool/validator.py`、vhost |
| 5 | 任意文件读取 | nginx root 固定；无 alias 拼接；根路径与目录 404 | vhost |
| 6 | 任意文件写入 | 公网仅 `GET`/`HEAD`（`limit_except`）；`pool` 只写隔离暂存区 | vhost + `scripts/install.sh` |
| 7 | 目录索引 / 版本泄露 | `autoindex off`；`server_tokens off`；根路径 404 | vhost |
| 8 | 未授权资产发布 | `pending` / `denied` / 无证据一律拒绝；无默认批准 | `pool/permissions.py` |
| 9 | 哈希污染 | 发布前 SHA256 必须与清单登记值一致；不一致拒绝并清理 | `pool/cli.py` `cmd_sync` |
| 10 | 伪装文件 / 脚本注入 | magic bytes 判定；仅 jpeg/png/gif/webp；SVG 拒绝 | `pool/validator.py` |
| 11 | 大小滥用 | `Content-Length` 预检 + 流式累计上限（默认 20 MiB） | `pool/downloader.py` |
| 12 | 不完整文件公开 | 暂存在公开根之外；校验后 `os.replace()` 原子发布；失败清理 | `pool/storage.py` |
| 13 | TLS 降级 | 下载器强制系统 CA 校验，无跳过开关；TLS 失败单独分类 | `pool/downloader.py` |
| 14 | 日志泄露 | 日志在 `/var/log/nginx` 与 `records_file`，不在公开根；nginx 404 兜底 | vhost |
| 15 | 流量滥用 | Pool 专属 access log + `traffic-stats.sh` 阈值统计 | `scripts/traffic-stats.sh` |
| 16 | 删除竞态 / 符号链接 | 删除前 `resolve_safe` + `is_symlink` 检查 + 物理 unlink | `pool/storage.py` `remove_file` |

---

## 4. 发布管线（sync）

```text
get_asset(asset_id)
  → check_permission（approved + evidence）
  → validate_source_url（https + 精确主机 + 路径后缀）
  → resolve_safe（字符校验 + realpath 落根）
  → 磁盘余量检查
  → 下载到公开根之外的暂存文件
  → 大小上限 → magic bytes → SHA256
  → 原子发布（同文件系统 os.replace；跨盘 .staging + fsync + replace）
  → 0444 只读 → 追加审计记录
```

任何一步失败都不会在公开目录留下文件；失败原因写入审计记录（若审计目录可用）。

---

## 5. 权限矩阵

| 路径 | 属主 | 权限 | 说明 |
|---|---|---|---|
| `/srv/pool/data` | `pool:pool` | `0755` | 公开根；`pool` 写，`www-data`/world 读 |
| 已发布文件 | `pool` | `0444` | 运行期只读 |
| `/srv/pool/tmp` | `pool:pool` | `0700` | 下载暂存区，公开根之外 |
| `/srv/pool/var` | `pool:pool` | `0750` | 审计记录目录 |
| `/etc/pool-poc/` | `root:pool` | `0750` | 配置与白名单目录 |
| `/etc/pool-poc/*.yaml` | `root:pool` | `0640` | 仅 root 与 pool 组可读 |
| `/opt/pool-poc/` | `root:pool` | `0755` / `0644` | 代码目录与文件 |

服务用户 `pool`：系统用户、`nologin`、无 `sudo`。nginx worker 以 `www-data` 运行。

---

## 6. 凭据管理

- 本仓库与脚本**不含**服务器密码、SSH 私钥、Token 或云厂商密钥。
- 部署脚本不落盘凭据；SSH 使用交互式密码或密钥。
- 生产配置 `config.yaml`、`assets.yaml` 不进入 Git；仓库只提交 `*.example.yaml`。
- 建议使用独立部署密钥，并限制其只能登录目标服务器。

---

## 7. 已知限制与残余风险

以下是**当前 `main` / V0.2 代码**的真实状态，不是“未来会解决”的承诺。

### 7.1 服务器层面（高 / 中）

1. **SSH 暴露（高）**：当前服务器为 `PermitRootLogin yes + PasswordAuthentication yes`，
   22 端口对公网开放。这是历史现状，不是 Pool 引入；建议改为密钥登录、禁用密码、
   限制来源或启用 fail2ban。Pool 部署不会自动修改 SSH 配置。
2. **无主机防火墙（中）**：iptables/nft 为空，所有端口公网可达。Pool 只新增 80/443
   （或回环 8080）监听；是否收紧整体防火墙由部署者决定。
3. **HSTS 未启用（低）**：待正式域名稳定后评估，避免临时域名被长期锁定。

### 7.2 Pool 代码层面（需部署者注意）

4. **`guard_system_disk` 在本分支是可选开关，默认关闭**：配置示例未默认开启。
   若数据盘掉挂载而护栏未启用，同步可能写入系统盘。**生产部署必须显式设置
   `guard_system_disk: true`**，并确认 `storage_root`/`tmp_dir`/审计目录均在数据盘。
5. **未使用受控目录句柄（guarded IO）**：本分支的路径安全依赖 `resolve_safe`
   （字符校验 + realpath）与删除前的符号链接检查；没有 `O_NOFOLLOW` 与打开后
   inode 复核。检查与写入之间的挂载点/符号链接竞态属于残余风险。开发线已实现
   更强保护，但**尚未合入本分支**。
6. **审计 JSONL 未加并发锁**：本分支的追加写是普通 `open(..., "a")`；
   并发 `sync` 可能导致记录交错。应串行执行同步操作。
7. **`expected_sha256` 依赖人工登记**：清单哈希的正确性由维护者从上游仓库核对。
   未登记时 CLI 只记录本地哈希并明确标注，不视为完成官方一致性验证。
8. **20 MiB 单文件上限**：超过上限的图片会被拒绝；调整需改配置并评估磁盘与流量。
9. **回环 HTTP 放宽**：配置示例对白名单内的 `127.0.0.1` 允许 `http://`，仅用于
   自生成测试资产；若不需要回环测试，生产配置应关闭 `allow_loopback_http`。
10. **流量统计 ≠ 计费流量**：`traffic-stats.sh` 只统计 Pool vhost 的发送字节；
    运营商按整机流量结算，可能包含 SSH、系统更新等。
11. **takedown 为手动**：没有自动联动机制，也不承诺小时级 SLA。
12. **临时域名**：`dl.bszx.site` 为测试域名；正式域名切换不影响存储与授权模型。

### 7.3 内容授权

13. 真实图片必须逐项 `approved` + 留证；未经站长明确授权，不托管、不发布真实作品。
    公网当前仅提供自生成测试图。

---

## 8. 部署检查清单

- [ ] `guard_system_disk: true`，且 `storage_root`、`tmp_dir`、`records_file` 都在数据盘；
- [ ] SSH 改为密钥登录，禁用 root 密码登录（或限制来源）；
- [ ] 按需启用主机防火墙，仅放行必要端口；
- [ ] `/etc/pool-poc` 为 `root:pool 0750`，YAML 为 `0640`；
- [ ] nginx 仅 `GET`/`HEAD`，根路径与目录 404，`server_tokens off`；
- [ ] TLS 证书链验证通过，TLS 1.2/1.3；
- [ ] 同步操作串行执行，避免并发写审计；
- [ ] 上线前 `verify` 所有已发布资产；
- [ ] 真实图片上线前取得站长逐项授权。

---

## 9. 审计与日志

- **同步/删除记录**：追加到配置的 `records_file`（示例
  `/srv/pool/var/sync-records.jsonl`），包含时间戳、资产、哈希来源、结果与原因。
- **访问日志**：`/var/log/nginx/pool-access.log`（Pool 专属格式），
  记录时间、路径、状态码、发送字节、耗时。
- **错误日志**：`/var/log/nginx/pool-error.log`。
- 删除是物理删除，对外路径立即 404；审计记录保留，不随文件删除。

日志目录不在公开根内；nginx 对 `.log` / `.jsonl` / `.yaml` 与隐藏文件有 404 兜底。

---

## 10. 报告安全问题

请通过仓库 Issue 联系维护者，**不要在公开 Issue 中粘贴可直接利用的细节**、
服务器凭据或未脱敏日志。维护者确认后会私下沟通修复与披露节奏。

本项目是独立社区设施，不提供漏洞赏金，也不承诺固定响应时限。

---

## 11. 后续加固（尚未进入本分支）

以下能力已在开发线完成或设计，但**不在 `main` / V0.2 中**，不应作为当前部署的安全保证：

- 受控目录句柄与 `O_NOFOLLOW`，发布/删除前后复核 inode 身份；
- `guard_system_disk` 默认开启，配置类型严格校验；
- 审计 JSONL 线程锁 + 跨进程锁 + 单次 write + fsync；
- 客户端分段下载的 Range/ETag/206 逐段校验与全文件 SHA256（v0.4-beta 发布线）；
- 内容源 origin 的集中配置与严格校验。

这些能力进入正式发布后，会同步更新本文件与版本说明。

---

## 12. 声明

PoolNode 是独立、只读、可撤销的辅助下载节点，不替代 GitHub Raw，不参与内容审核，
不承诺 SLA。所有真实图片的著作权归原作者；托管与展示以站长授权为准。
