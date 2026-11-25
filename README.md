# PoolNode

**面向 [蓝色大肥鱼.com](https://蓝色大肥鱼.com) 的独立、只读、可撤销备用原图下载节点。**

在线节点：**https://dl.bszx.site**

PoolNode 与现有 GitHub Raw 发布体系**完全解耦**：不替换 Raw、不改原站代码、不改已发布 URL。
它只做一件事——把**经站长明确授权**的原图，以与原仓库一致的路径，稳定地提供为可直接下载的
HTTPS 静态文件。

> 当前状态：服务端 **V0.2** 运行中；分段下载客户端 **v0.4-beta** 在独立发布线维护；
> 真实图片托管**等待站长授权**，公网目前仅提供自生成测试图。

---

## 直接下载

已经发布的文件可以像 Raw 一样直接取用，URL 只把域名换掉：

```text
https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/blue-maid.png
                                   ↓
https://dl.bszx.site/owner-picks/blue-maid.png
```

公网目前提供的是**自生成测试图**，可直接验证：

| 文件 | URL | 字节数 | SHA-256 |
|---|---|---:|---|
| 冒烟测试图 | `https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg` | 1,778,514 | `b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e` |
| 2 MB 基准图 | `https://dl.bszx.site/pool-tests/bench-2m.jpg` | 2,112,525 | `cb7926ffb9bb13c6a48efffd22412353ea6450d322feb6be1147ee6f204b6530` |
| 5.4 MB 基准图 | `https://dl.bszx.site/pool-tests/bench-6m.jpg` | 5,403,720 | `294d65cc0751226ef6691e5645f5625f09d8c24b6389e3f451d467b38f6359ed` |

```bash
curl -fLO https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg
sha256sum pool-public-smoke-test.jpg
```

节点只提供 `GET` / `HEAD`，原生支持 `Range` / `206` / 强 `ETag` / `If-Range`，
不提供上传、目录索引或动态接口。

---

## 它是什么，不是什么

**是：**

- 一个独立的只读下载节点，路径与 `lmy414/ai-girl-stickers` 仓一比一对应；
- 一个带白名单、SHA256、格式与大小校验的发布管线；
- 一个可随时撤下、不掌握审核权的辅助设施。

**不是：**

- 不是 CDN，不承诺任何速度或可用性 SLA；
- 不镜像全站，不抓取、不转载未授权内容；
- 不替代 Raw，也不改变原站任何已发布 URL；
- 不参与审核、推荐、搜索或内容治理；
- 不接受公网上传，不提供写入接口。

---

## 为什么需要它

`raw.githubusercontent.com` 在中国大陆不是“慢”，而是在部分网络链路上**不可达**：
移动网络可能可达，广电网络可能 DNS 投毒，家宽直连可能被 RST。对创作者来说，
“下载原图”这一步断掉，后面的二创与投稿都无从开始。

PoolNode 在香港部署只读节点。2026-11-21 晚高峰实测显示，回程存在明显的 **per-flow 整形**：
单流被压到 0.4–0.5 Mbps，而 16 段并发中位约 4.54 Mbps，32/64 流传输层探针聚合
10.84 / 22.17 Mbps，未观察到总量拐点。这也是 v0.4-beta 分段并发客户端的由来。

> 以上是特定时间、特定路径的测量结果，不是速度承诺。完整方法与原始数据见
> [docs/IMAGE_DOWNLOAD_REPORT.md](docs/IMAGE_DOWNLOAD_REPORT.md)。

### 下载客户端（v0.4-beta）

分段并发客户端 `pool.fetch` 在**独立发布线**维护，尚未合入本分支（`main` 只包含服务端 V0.2）。
它只接受 `asset_id`，按维护者签发的清单校验 SHA256，并针对晚高峰按段并发下载，
适合从命令行批量获取原图。使用方式与协议细节以该发布线随附的说明为准。

---

## 部署服务端

服务端是维护者工具，不是面向公众的写入服务。生产部署需要一台独立服务器与数据盘。

```bash
# 在仓库根目录
sudo bash scripts/install.sh --mount     # 安装依赖、目录、数据盘挂载
sudo bash scripts/deploy.sh              # 部署代码与 nginx vhost（默认仅回环）

# 配置 /etc/pool-poc/config.yaml 与 /etc/pool-poc/assets.yaml 后：
python3 -m pool --config /etc/pool-poc/config.yaml check-upstream <asset_id>
python3 -m pool --config /etc/pool-poc/config.yaml sync <asset_id>
python3 -m pool --config /etc/pool-poc/config.yaml verify <asset_id>
python3 -m pool --config /etc/pool-poc/config.yaml remove <asset_id>
```

| 命令 | 作用 |
|---|---|
| `check-upstream` | DNS/TCP/TLS/HEAD/GET 分阶段诊断，只取样、不落盘 |
| `sync` | 授权 → URL 校验 → 下载 → 格式/大小/SHA256 → 原子发布 → 审计 |
| `verify` | 校验已发布文件与官方哈希/同步记录一致 |
| `remove` | 删除物理文件并留审计记录（takedown） |

完整部署、TLS、回滚与目录约定见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

---

## 安全与授权模型

### 信任锚是清单，不是 URL

- CLI 只接受 `asset_id`；真实 URL 只来自维护者签发的 `assets.yaml`。
- 资产必须 `permission_status: approved` 且 `permission_evidence` 非空，才允许同步。
- 白名单未登记 `expected_sha256` 时，只做本地哈希记录，不冒充官方一致性验证。

### 传输与内容校验

- 上游强制 HTTPS，证书校验始终开启，没有跳过开关。
- 上游主机必须**精确命中**白名单，重定向逐跳重新校验。
- 文件格式按 magic bytes 判定，只允许 jpeg/png/gif/webp；SVG 明确拒绝。
- 下载完成后校验大小与 SHA256；任何不一致都不会发布。

### 写入与发布

- 下载先落在公开目录之外的暂存区；校验通过后同文件系统原子发布。
- 支持可选的系统盘护栏 `guard_system_disk`：启用后，数据盘未挂载或与系统盘同设备时
  拒绝写入。**生产配置应显式设为 `true`。**
- 公网只读：nginx 仅放行 `GET` / `HEAD`，根路径与目录返回 404。
- 删除是物理删除，并追加审计记录。

### takedown

站长提出后，由维护者**手动执行** `python3 -m pool remove <asset_id>` 摘除并留痕。
当前**没有**自动联动机制，也不承诺小时级 SLA。

完整威胁模型、权限矩阵与残余风险见 [docs/SECURITY.md](docs/SECURITY.md)。

---

## 架构

```text
维护者
  assets.yaml（approved + evidence + expected_sha256）
        │
        ▼
  python3 -m pool sync
  授权 → URL 校验 → 下载 → 格式/大小/SHA256 → 原子发布
        │
        ▼
  /srv/pool/data/<repo 相对路径>        ← 独立数据盘
        │
        ▼
  nginx（只读，仅 GET/HEAD，原生 Range/206/ETag）
        │
        ▼
  https://dl.bszx.site/<repo 相对路径>
```

收录路径以白名单实际登记为准：`dist/submissions/originals/<角色>/<文件名>`、
`owner-picks/<文件名>`、`blue-fish-originals/<文件名>`。

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 版本与发布边界

| 层级 | 版本 | 状态 |
|---|---|---|
| 服务端 | **V0.2** | 本分支（`main`）运行版本；生产环境冻结 |
| 客户端 | **v0.4-beta** | 独立发布线维护，未合入 `main` |
| 候选版本 | **v0.5.0-rc.1** | 未发布 |
| 开发构建 | **0.6.0.dev1** | 开发线构建；不是 Alpha，不是正式发行 |

服务端与客户端版本解耦：**静态 Range 协议是唯一契约**。
服务端升级不会要求客户端同步升级；客户端发布也不会改动生产节点。

---

## 项目结构

```text
config/     配置示例与资产白名单示例
pool/       Python 源码（config / permissions / validator / downloader / storage / cli）
scripts/    安装、部署、验收、测速、流量统计、资产维护
tests/      pytest 自动化测试（离线，不访问公网）
docs/       架构、部署、安全、测量报告、验收记录
```

---

## 开发与测试

要求 **Python ≥ 3.10**。

```bash
python -m pip install -r requirements.txt pytest
python -m pytest tests/ -q
python -m compileall -q pool scripts tests
```

- 当前 `main` 测试集：**77 passed / 1 skipped**（Windows 下创建符号链接需特权，未计为通过）。
- 测试默认使用本地回环服务器与自生成图片，不访问生产节点。

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 服务端架构与信任边界 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 安装、nginx、TLS、回滚 |
| [docs/SECURITY.md](docs/SECURITY.md) | 威胁模型、权限矩阵、残余风险 |
| [docs/SECURITY_AUDIT_V02.md](docs/SECURITY_AUDIT_V02.md) | V0.2 公网安全审计记录 |
| [docs/IMAGE_DOWNLOAD_REPORT.md](docs/IMAGE_DOWNLOAD_REPORT.md) | 晚高峰根因、剂量-响应与复算 |
| [docs/CHINA-TEST.md](docs/CHINA-TEST.md) | 大陆网络实测方法 |
| [docs/NETWORK_TESTING.md](docs/NETWORK_TESTING.md) | 网络测试工具与记录 |
| [docs/TLS_DEPLOYMENT.md](docs/TLS_DEPLOYMENT.md) | 证书签发与 HTTPS 部署 |
| [docs/V02_TEST_REPORT.md](docs/V02_TEST_REPORT.md) | V0.2 公网验收 33/33 |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | V0.1 自动化测试记录 |

---

## 当前进度

- [x] V0.1 内部 PoC：核心 CLI + 回环静态服务
- [x] V0.2 公网 HTTPS：**https://dl.bszx.site**（临时测试域名）
- [x] 晚高峰根因实测与分段并发客户端（v0.4-beta，独立发布线）
- [x] 40 张自生成 Demo 图走完整发布管线
- [x] 公网安全边界：只读、白名单、SHA256、原子发布
- [ ] 真实图片托管：**等待站长明确授权**
- [ ] 联通/电信/广电多网络样本：当前仅中国移动宽带单点
- [ ] 正式域名：临时域名切换成本已最小化
- [ ] v0.5 正式发布：候选版本需完成真实网络复测
- [ ] v0.6 自适应调度：设计阶段

---

## 许可与图片版权

本仓库的 **MIT 许可仅覆盖 PoolNode 自身代码与文档**。

任何图片的著作权始终归原作者。图片的授权、署名与使用条件以白名单中对应资产的
`attribution / license / permission_evidence` 字段为准。未经站长明确批准
（`approved` + 留证）的资产不会被同步、发布或对外提供。

公网 Demo 图池为项目自生成测试图，无第三方版权内容。

---

PoolNode 是独立社区项目，与 蓝色大肥鱼.com 官方无隶属关系。
真实图片托管、域名与展示方式均以站长授权为准。
