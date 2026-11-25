# PoolNode — 蓝色大肥鱼.com 独立备用原图下载节点

> 原名 *Pool PoC*。面向 [蓝色大肥鱼.com](https://蓝色大肥鱼.com) 的**独立、只读、可撤销**的
> 备用原图下载服务。与现有 GitHub Raw 发布体系完全解耦：不替换 Raw、不改原站代码、
> 不改已发布 URL。

**许可说明**：本仓库的 MIT 许可仅覆盖 Pool 自身代码与文档。
任何图片文件的授权以 `/etc/pool-poc/assets.yaml` 中各资产的
`attribution / license / permission_evidence` 字段为准 —— 图片著作权始终归原作者，
未经站长明确批准（approved + 留证）的资产不会被同步或发布。

---

## 架构一览

```
pool-poc CLI（授权→下载→SHA256→原子发布）
        ↓
/srv/pool/data/<repo相对路径>    ← 50G 数据盘，Nginx 只读静态服务
        ↓
https://<POOL_DOMAIN>/<repo相对路径>

pool.fetch（客户端，V0.4 新增）    ← 晚高峰分段并发下载器（见下）
```

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；从源码接手请看
[源码导览](docs/CODE_ARCHITECTURE_MAP.md) 和 [维护风格](docs/CODE_STYLE_GUIDE.md)。

## 版本史

| 版本 | 内容 | 证据 |
|---|---|---|
| V0.1 | 内部 PoC：核心 CLI（授权/下载/校验/原子发布）+ 回环静态服务 | docs/TEST_REPORT.md |
| V0.2 | 公网 HTTPS 上线 **https://dl.bszx.site**（临时测试域名）：DNS/证书/301/自动续期；公网验收 33/33 | docs/TLS_DEPLOYMENT.md · docs/V02_TEST_REPORT.md |
| V0.2.x | 晚高峰回程实测：PCCW per-flow 整形（单流 0.5 Mbps，16 流并发 6.35 Mbps）→ 根因/解法矩阵/路线图 | docs/IMAGE_DOWNLOAD_REPORT.md |
| V0.4-beta | **峰值客户端 `pool.fetch`**（16 段 Range + If-Range/ETag/206 校验 + 单链路降级）+ 40 张 Demo 图池 + 晚高峰基准（22:48–23:25，7 工况 × 5 rep） | docs/PEAK_DOWNLOAD_CLIENT.md · reports/benchmarks/v04-beta-peak/ · reports/demo/ |
| v0.5.0-rc.1 | 候选：动态分块队列 + 连接复用 + T1–T5；策略对比已勘误，未重新验收 | docs/V05_DYNAMIC_SCHEDULER.md · reports/benchmarks/v05-rc1/ERRATA_2026-11-26.md |
| 0.6.0.dev1 | **当前分支内部构建**：v0.5 调度 + 安全基线 + 测量工具；自适应算法尚未实现，**不是** Alpha 发布 | docs/V06_DESIGN.md · docs/VERSION_POLICY.md |

> **发布边界**：服务端生产为 **V0.2（冻结）**，正式客户端分发为 **v0.4-beta**。
> `v0.5.0-rc.1` 仅本地 tag，未发布；`0.6.0.dev1` 为内部开发构建，未授权 Alpha。
> 版本层级与单一来源见 [docs/VERSION_POLICY.md](docs/VERSION_POLICY.md)。
>
> **资产口径**：bench-6m = 5,403,720 B（十进制 5.40 MB / 二进制 5.15 MiB）。历史文档中的
> “5.15MB”“5.4 MiB”“5.4MB”均指同一文件，比较时以字节数为准。

## 快速开始（本地开发）

要求 **Python ≥ 3.10**（CI 覆盖 3.10–3.14，Ubuntu + Windows）。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/          # 当前 243 passed / 1 skipped
```

## 快速开始（服务器部署）

```bash
# 1. 上传项目到服务器 /opt/pool-poc/src（scp/sftp 均可）
# 2. 安装系统依赖与目录（含挂载 50G 数据盘）
sudo bash scripts/install.sh --mount
# 3. 部署代码与配置（默认回环模式，域名就绪前不公网暴露）
sudo bash scripts/deploy.sh
# 4. 同步测试资产并验证
python3 -m pool --config /etc/pool-poc/config.yaml sync test-poc-001
bash scripts/smoke-test.sh
```

完整手册：[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · 回滚见其末节。

## CLI

### 服务端

| 命令 | 作用 |
| --- | --- |
| `pool-poc check-upstream <asset_id>` | 上游连通性诊断（DNS/TCP/TLS/HTTP 分阶段，不落盘）|
| `pool-poc sync <asset_id>` | 授权检查→流式下载→格式/大小/SHA256 校验→原子发布 |
| `pool-poc verify <asset_id>` | 校验已发布文件与官方哈希/同步记录的一致性 |
| `pool-poc remove <asset_id>` | 删除白名单资产对应的物理文件（留痕）|

### 客户端（V0.4 新增）

| 命令 | 作用 |
| --- | --- |
| `python -m pool.fetch <asset_id> <out> --assets <白名单.yaml>` | 晚高峰分段并发下载：按文件大小选 1/4/16 段，Range+If-Range+ETag+206 逐段校验，SHA256 全件比对，失败自动降级单链路 |

细则与边界：[docs/PEAK_DOWNLOAD_CLIENT.md](docs/PEAK_DOWNLOAD_CLIENT.md)

## URL 结构

URL = 图片在 [lmy414/ai-girl-stickers](https://github.com/lmy414/ai-girl-stickers)
仓库中的相对路径，与 GitHub Raw 一比一对应：

```
https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/blue-maid.png
                                                              ↓ 换域名即可
https://<POOL_DOMAIN>/owner-picks/blue-maid.png
```

收录的路径：`dist/submissions/originals/<角色>/<文件名>`、`owner-picks/<文件名>`、
`blue-fish-originals/<文件名>`（以白名单实际登记为准）。

## 目录

```
config/    配置示例（config / assets 白名单）
pool/      Python 源码
           ├─ config / permissions / validator / downloader / storage / cli   （服务端）
           └─ fetch / range_transport                                          （客户端，V0.4）
scripts/   install / deploy / smoke-test / public-acceptance / traffic-stats
           / remove-asset / logrotate / china-speedtest（Windows + Linux/macOS）
           / gen-test-image / gen-test-jpeg
tests/     pytest 自动化测试
reports/   benchmarks（晚高峰基准）/ demo（40 图清单）/ network-tests（三网样本）
docs/      ARCHITECTURE / DEPLOYMENT / SECURITY / TLS_DEPLOYMENT / CHINA-TEST
           / PEAK_DOWNLOAD_CLIENT / IMAGE_DOWNLOAD_REPORT / OPTIMIZATION_ROADMAP
           / V02_TEST_REPORT / TEST_REPORT 等
```

## 状态

- [x] V0.1 内部 PoC：核心实现 + 回环静态服务
- [x] V0.2 公网 HTTPS：**https://dl.bszx.site**（临时测试域名，切换成本已最小化）
  - 公网验收 33/33：GET/HEAD/Range/SHA256 三方闭环/安全边界
- [x] 晚高峰根因实测：回程 PCCW per-flow 整形（单流 0.5 Mbps；并发剂量实验 1→16 流线性，
  见 docs/IMAGE_DOWNLOAD_REPORT.md §3.5）
- [x] V0.4-beta 峰值客户端 + 晚高峰基准：`pool.fetch` 16 段实测 3.6–4.65 Mbps（单流 0.4 的
  ~10 倍）；curl 32/64 流复算后聚合 10.84/22.17 Mbps、**线性扩展至 64 流无拐点**（初版
  "反 flood 悬崖"结论作废，详见 reports/benchmarks/v04-beta-peak/NOTE.md）
- [x] Demo 图池：40 张自生成图（1–4 MB 梯度）已走完整 sync 管线发布，清单与 SHA256 见
  reports/demo/pool-demo-urls-20261122.md
- [ ] 联通/电信/广电样本：**NOT TESTED**（当前仅中国移动宽带单客户端）
- [ ] 真实图片托管：**等待站长明确授权**（公网目前仅自生成测试图片）
- [ ] 正式域名：临时域名切换成本已最小化（域名不入核心逻辑）

