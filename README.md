# PoolNode

**面向 [蓝色大肥鱼.com](https://蓝色大肥鱼.com) 的独立、只读、可撤销备用原图下载节点。**

在线节点：**https://dl.bszx.site**

PoolNode 与现有 GitHub Raw 发布体系**完全解耦**：不替换 Raw、不改原站代码、不改已发布 URL。
它只做一件事——把**经站长明确授权**的原图，以与原仓库一致的路径，稳定地提供为可直接下载的 HTTPS 静态文件。

> 当前状态：服务端 **V0.2** 运行中；正式客户端 **v0.4-beta**；真实图片托管**等待站长授权**，
> 公网目前仅提供自生成测试图。版本边界见 [docs/VERSION_POLICY.md](docs/VERSION_POLICY.md)。

---

## 它是什么，不是什么

**是：**

- 一个独立的只读下载节点，路径与 `lmy414/ai-girl-stickers` 仓一比一对应；
- 一个带白名单、SHA256、格式与大小校验的发布管线；
- 一个针对“晚高峰单流被限速”场景设计的分段并发下载客户端；
- 一个可随时撤下的、不掌握审核权的辅助设施。

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

PoolNode 在香港部署只读节点，并针对晚高峰回程的 **per-flow 整形**做了客户端分段并发：
单流被压到 0.4–0.5 Mbps 时，多连接聚合仍能随流数近似线性上升。v0.4-beta 客户端在
2026-11-21 晚高峰实测中，5.4 MB 文件 16 段并发中位 **4.54 Mbps**，约为单流的 10 倍；
32/64 流传输层探针聚合 **10.84 / 22.17 Mbps**，未观察到总量拐点。

> 以上是特定时间、特定路径的测量结果，不是速度承诺。完整方法与原始数据见
> [docs/IMAGE_DOWNLOAD_REPORT.md](docs/IMAGE_DOWNLOAD_REPORT.md) 与
> [reports/benchmarks/v04-beta-peak/](reports/benchmarks/v04-beta-peak/)。

---

## 使用客户端下载

要求 **Python ≥ 3.10**，运行时只依赖 PyYAML。

```bash
git clone https://github.com/QingjiuAce/PoolNode-Dev.git
cd PoolNode-Dev
python -m pip install -r requirements.txt

# 用仓库内的自生成测试资产做一次完整下载（含 SHA256 全件校验）
python -m pool.fetch pool-public-smoke-test ./demo.jpg \
  --assets config/bench-assets.yaml
```

成功后会打印校验通过的 SHA256 与输出路径。客户端**只接受 `asset_id`**，
不接受任意 URL；`asset_id` 必须存在于维护者签发的 `assets.yaml` 中，
且清单内登记了 `expected_sha256`。

### 常用参数

| 参数 | 说明 |
|---|---|
| `--workers 1..16` | 并发段数上限；`1` 强制单链接。默认按文件大小选 1/4/16 |
| `--scheduler auto\|static\|dynamic` | `auto`（默认）：≥4 MiB 用动态分块队列，小文件用 v0.4 固定等分 |
| `--metrics <path>` | 写入逐请求阶段计时（DNS/TCP/TLS/TTFB/Body）；文件已存在时拒绝覆盖 |
| `--json` | stdout 输出单行机器可读 JSON |
| `--origin <url>` | 自定义内容源（默认 `https://dl.bszx.site`，也可用 `POOL_ORIGIN`） |

客户端行为：

1. 读取 `assets.yaml`，只按 `asset_id` 查找，拒绝未授权或无 SHA256 的资产；
2. 先发 1 字节 Range 探测，确认服务端返回强 ETag 与 206；
3. 按段并发下载，每段校验 `Content-Range` / `ETag` / `If-Range` / 字节数；
4. 按字节偏移写入预分配文件，不做文件名排序；
5. 全文件大小 + SHA256 校验通过后，才在本地原子创建目标文件；
6. 任一段失败或校验不符，丢弃暂存并降级为单链路重试一次；证书错误、重定向、本地 I/O 错误直接失败。

细则与失败语义见 [docs/PEAK_DOWNLOAD_CLIENT.md](docs/PEAK_DOWNLOAD_CLIENT.md)。

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

- 客户端与服务端都只接受 `asset_id`；真实 URL 只来自维护者签发的 `assets.yaml`。
- 资产必须 `permission_status: approved` 且 `permission_evidence` 非空，才允许同步。
- 必须登记 `expected_sha256`；没有官方哈希的资产不会被客户端下载。

### 传输与内容校验

- 上游与下载节点均强制 HTTPS，证书校验始终开启，绝不降级。
- 上游主机必须**精确命中**白名单，重定向逐跳重新校验。
- 文件格式按 magic bytes 判定，只允许 jpeg/png/gif/webp；SVG 明确拒绝。
- 下载完成后校验大小与 SHA256；任何不一致都不会发布。

### 写入与发布

- 下载先落在公开目录之外的暂存区；校验通过后同文件系统原子发布。
- `guard_system_disk` 默认开启：数据盘未挂载、设备不可确认或与系统盘同设备时
  **拒绝写入**，避免把数据误写进系统盘。
- 暂存、发布、删除均使用受控目录句柄与 inode 身份复核，拒绝符号链接与挂载点竞态。
- 审计日志为 JSONL，追加写入并 fsync；并发写入有线程锁与跨进程锁保护。

### takedown

站长提出后，由维护者**手动执行** `python3 -m pool remove <asset_id>` 摘除并留痕。
当前**没有**自动联动机制，也不承诺小时级 SLA。

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

用户
  pool.fetch（asset_id）→ HTTPS 分段 Range → SHA256 全件校验 → 本地文件
```

URL 与 Raw 一比一对应，只换域名：

```text
https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/blue-maid.png
https://dl.bszx.site/owner-picks/blue-maid.png
```

收录路径以白名单实际登记为准：`dist/submissions/originals/<角色>/<文件名>`、
`owner-picks/<文件名>`、`blue-fish-originals/<文件名>`。

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；
源码导览见 [docs/CODE_ARCHITECTURE_MAP.md](docs/CODE_ARCHITECTURE_MAP.md)。

---

## 版本与发布边界

| 层级 | 版本 | 状态 |
|---|---|---|
| 服务端生产 | **V0.2** | 运行中，冻结；仓库后续开发不会自动部署 |
| 正式客户端分发 | **v0.4-beta** | 当前唯一正式分发版本 |
| 候选版本 | **v0.5.0-rc.1** | 未发布；策略对比已勘误，尚未重新验收 |
| 开发构建 | **0.6.0.dev1** | 仓库当前构建；不是 Alpha，不是正式发行 |

- `pool/__init__.py` 的 `__version__` 是唯一版本来源；CLI `--version` 与 User-Agent 均由它派生。
- 服务端与客户端版本解耦：静态 Range 协议是唯一契约。
- 详细政策：[docs/VERSION_POLICY.md](docs/VERSION_POLICY.md)。

---

## 项目结构

```text
config/     配置示例与资产白名单示例
pool/       Python 源码
            ├─ config / permissions / validator / downloader / storage / cli   （服务端）
            └─ fetch / range_transport / origin                                 （客户端）
scripts/    安装、部署、验收、测速、探针、资产维护
tests/      pytest 自动化测试（离线，不访问公网）
reports/    性能基准、Demo 清单、网络实测原始记录
docs/       架构、部署、安全、客户端、测量报告、版本政策
```

---

## 开发与测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
python -m compileall -q pool scripts tests
```

- 支持 Python 3.10–3.14；CI 工作流配置覆盖 Ubuntu 与 Windows。
- 测试默认使用本地回环服务器与自生成图片，不访问生产节点。
- 当前测试集：**268 passed / 1 skipped**（Windows 下创建符号链接需特权，未计为通过）。

---

## 文档

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 服务端/客户端架构与信任边界 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 安装、nginx、TLS、回滚 |
| [docs/SECURITY.md](docs/SECURITY.md) | 安全模型与已知边界 |
| [docs/PEAK_DOWNLOAD_CLIENT.md](docs/PEAK_DOWNLOAD_CLIENT.md) | 客户端协议、失败语义、分段策略 |
| [docs/IMAGE_DOWNLOAD_REPORT.md](docs/IMAGE_DOWNLOAD_REPORT.md) | 晚高峰根因、剂量-响应、复算与勘误 |
| [docs/VERSION_POLICY.md](docs/VERSION_POLICY.md) | 版本来源与发布边界 |
| [docs/CHINA-TEST.md](docs/CHINA-TEST.md) | 大陆网络实测方法 |
| [docs/CODE_ARCHITECTURE_MAP.md](docs/CODE_ARCHITECTURE_MAP.md) | 源码导览 |
| [docs/CODE_STYLE_GUIDE.md](docs/CODE_STYLE_GUIDE.md) | 维护风格 |

---

## 当前进度

- [x] V0.1 内部 PoC：核心 CLI + 回环静态服务
- [x] V0.2 公网 HTTPS：**https://dl.bszx.site**（临时测试域名）
- [x] 晚高峰根因实测与分段并发客户端（v0.4-beta）
- [x] 40 张自生成 Demo 图走完整发布管线
- [x] 安全基线：白名单、SHA256、原子发布、系统盘护栏
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
