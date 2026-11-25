# 分支清单（阶段 0）

> 时点：2026-11-26 03:06 +0800。所有信息来自 `git branch -avv` / `git log`。

## 1. 本地分支

| 分支 | HEAD | 上游 | 领先/落后 | 尖端时间 | 说明 |
|---|---|---:|---|---|---|
| `feature/v0.6-alpha` | `1e38122` | 无 | 本地独有 | 2026-11-26 00:01 | **本次整改目标分支**；含 2026-11-25 数据收口 |
| `dev` | `75e65b1` | `origin/dev` | +14 / −0 | 2026-11-25 01:52 | 集成分支；含安全加固 + RC 合并；**不得部署** |
| `release/v0.5` | `0376444` | 无 | 本地独有 | 2026-11-25 01:53 | RC 基线，`v0.5.0-rc.1` 指向此提交 |
| `investigate/v05-chunk-queue` | `98a4e0a` | 无 | 本地独有 | 2026-11-26 00:40 | 调查分支；alpha + 3 个实验提交 |
| `feature/peak-probe-tooling` | `b27e8d6` | `origin/feature/peak-probe-tooling` | +7 / −0 | 2026-11-25 01:13 | 测量工具与审计文档 |
| `feature/v0.4-beta` | `6b2338f` | `origin/feature/v0.4-beta` | +1 / −0 | 2026-11-23 01:41 | v0.4-beta 发行线 |
| `main` | `147dd40` | `origin/main` | 0 / 0 | 2026-11-21 21:49 | 报告与规划 |
| `master` | `63c0ef8` | 无 | 本地独有 | 2026-11-21 07:29 | V0.2 生产历史 |
| `fix/guard-system-disk-default` | `62b0cb8` | 无 | 本地独有 | 2026-11-21 08:52 | 安全加固分支（已含于 dev） |
| `fix/guard-system-disk-race-hardening` | `70411f6` | `origin/dev` 同点 | 0 / 0 | 2026-11-21 09:41 | 安全加固尖端（已含于 dev） |
| `maintenance/code-quality-v01` | `fe03d54` | 无 | 本地独有 | 2026-11-21 08:16 | 代码质量重构（已含于 dev） |

## 2. 拓扑关系

| 关系 | 共同祖先 |
|---|---|
| `feature/v0.6-alpha` ↔ `dev` | `6b2338f`（feature/v0.4-beta 尖端） |
| `feature/v0.6-alpha` ↔ `release/v0.5` | `6b2338f` |
| `release/v0.5` ↔ `main` | `147dd40` |
| `dev` ↔ `main` | `147dd40` |
| `investigate/v05-chunk-queue` ↔ `feature/v0.6-alpha` | `1e38122`（alpha 当前 HEAD） |

## 3. 关键分支独有提交

### 3.1 `dev` 有、`feature/v0.6-alpha` 没有（9 个）

| 提交 | 类型 | 内容 | 整改处置 |
|---|---|---|---|
| `fe03d54` | 重构 | 代码注释/边界澄清 + `tests/test_compatibility.py` | 随安全基线一并移植（安全前置） |
| `7ac2621` | 安全 | `guard_system_disk` 默认开启 + 系统盘护栏测试 | **必须移植** |
| `62b0cb8` | 运维文档 | sshd host-key 事件记录 + 性能基线文档 | 文档，按需保留 |
| `70411f6` | 安全 | guarded IO + 原子发布/审计加固 + mount race 测试 | **必须移植** |
| `884db00` | 修复 | T1–T5（cancel API / setup error / retry 计数） | 与 alpha `be489a3` **内容等价** |
| `404cb33` | 文档 | RC 发布说明 + 版本解耦政策 | 已有等价文档 |
| `36c45b7` | 审查文档 | V0.5 代码审查 + auto 解析测试 | 已有等价文档/测试 |
| `9b53e7c` | 文档 | 测试数勘误（102 passed） | 已有等价文档 |
| `75e65b1` | 合并 | release/v0.5 → dev（仅集成） | 不移植（合并提交） |

### 3.2 `feature/v0.6-alpha` 有、`dev` 没有（13 个）

`47952eb`、`a48331f`、`1a9c825`、`e1847f3`、`be52474`、`9167de1`、`be489a3`（T1–T5，等价于 `884db00`）、`5bd92a4`、`12f01bb`、`b27e8d6`、`1768d90`、`4fe0daf`、`1e38122`。

即 alpha 侧独有的是测量工具、实验数据、审计/设计文档与 T1–T5；安全加固和重构只在 dev 侧。

## 4. Tag

| Tag | 类型 | 对象 | 目标提交 | 备注 |
|---|---|---|---|---|
| `v0.5.0-rc.1` | annotated | `24317ae` | `0376444` | 仅限有限实测；非正式发行；远端无此 tag |

附注正文存在历史编码损坏（`release candidate` 前的破折号显示为 `鈥�`），不影响指向与校验；整改**不移动、不重建**该 tag。

## 5. 结论

- 安全基线的断层是**分支拓扑问题**：alpha 从 v0.4-beta 线派生，天然不含 dev 的 70411f6 系列。
- T1–T5 不存在双实现：alpha `be489a3` 与 release `884db00` 对 `pool/range_transport.py`、`tests/test_peak_download.py` 的 diff 为空。
- 因此阶段 3 的最小整合面是：`pool/_guarded_io.py`（新增）+ `cli/config/storage/validator/downloader/permissions` 的 dev 版本 + 对应安全测试与 `config.example.yaml`；不需要整分支合并。
