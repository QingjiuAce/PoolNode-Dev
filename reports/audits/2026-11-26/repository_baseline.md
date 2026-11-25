# 仓库基线审计（阶段 0）

> 审计开始：2026-11-26 03:06 +0800
> 目标分支：`feature/v0.6-alpha`
> 原则：本文件只记录**实际仓库状态**；与任务书引用的检查报告不一致处，以仓库为准并单列。

## 1. 环境

| 项 | 值 |
|---|---|
| 工作目录 | `F:\鲸鲸\GitHub\开源二创\Pool` |
| 平台 | Windows 10.0.26300 x64 / Git Bash (MINGW64) |
| Python | 3.14.5 |
| pytest | 9.2.0 |
| Git 远端 | `origin` = `https://github.com/QingjiuAce/PoolNode-Dev.git` |
| 审计执行者 | 主执行模型（无子代理参与修改） |

## 2. Git 基线

| 项 | 值 |
|---|---|
| 当前分支 | `feature/v0.6-alpha` |
| HEAD | `1e38122`（`data(2026-11-25): closeout archive — 326 arms, 326/326 SHA`，2026-11-26 00:01:41 +0800） |
| 工作区 | **11 个已跟踪文件修改，0 暂存，0 未跟踪** |
| 本地 Tag | `v0.5.0-rc.1`（附注 tag，tag 对象 `24317ae` → commit `0376444`） |
| 远程 Tag | `git ls-remote --tags origin` 无输出 → **远端没有任何 Tag** |
| 分支总数 | 11 个本地分支（含 master 历史线） |

### 2.1 未提交改动明细

全部改动均为实验数据，不含源码：

| 文件 | HEAD 行数 | 工作区行数 | 性质 |
|---|---:|---:|---|
| `reports/benchmarks/cmin-experiment/runs.jsonl` | 160 | 200 | **追加**（2026-11-26 01:15 档 40 行），合法 |
| `reports/benchmarks/v05-rc1/metrics/1.jsonl` … `10.jsonl` | 各 24 | 各 24 | **整文件覆盖**（健康窗 → 劣化窗），P0-3 缺陷实例 |

- `v05-rc1/runs.jsonl` 未被修改（`git status` 未列出）。
- 覆盖写判定依据：同一路径同一行数、时间戳从 `1795543199…`（11-25 约 01:59）整体变为 `1795620302…`（11-25 约 23:25），且内容逐行为不同请求记录。详见阶段 1 恢复档案。

### 2.2 与检查报告的关系

任务书引用的《Pool 项目全面检查报告（2026-11-26）》未随仓库提供，本次以仓库实际内容重新核验。已复核一致的结论：

1. `feature/v0.6-alpha` 的 `pool/` 树与 `v0.5.0-rc.1` **完全相同**（`82966c49d2da8d4103253f46dd27c192e04d4b5f`），缺 `dev` 的 9 个提交。
2. `v0.5.0-rc.1` 是附注 tag，指向 `0376444`。
3. 10 个 RC metrics 文件确为覆盖式改写。
4. RC Harness 的 B/D 两臂策略配置错误（阶段 2 修复）。
5. 仓库无 `.github/workflows/`（无 CI），版本标识为 `0.1.0`（阶段 5 处理）。

与报告可能存在的差异：`feature/v0.6-alpha` 当前 HEAD 已推进到 `1e38122`（含 2026-11-25 数据收口提交），若报告基于更早的 `4fe0daf`，其"HEAD"描述已过期；内容结论不受影响。

## 3. 分支拓扑

- 所有开发分支的共同祖先：`147dd40`（`main`，2026-11-21）。
- `release/v0.5` 与 `dev` 均从 `147dd40` 派生；`dev` 在 `70411f6`（安全加固）之上合并了 `release/v0.5`，合并提交 `75e65b1`。
- `feature/v0.6-alpha` 从 `6b2338f`（`feature/v0.4-beta` 尖端）派生，**与 `dev` 的最后共同祖先是 `6b2338f`**，因此完全不含 `70411f6` 系列安全提交。
- `investigate/v05-chunk-queue` 以 `feature/v0.6-alpha` @ `1e38122` 为基，领先 3 个仅本地的调查提交。

详见 `branch_inventory.md` 与 `raw/graph.txt`。

## 4. 风险登记（阶段 0 时点）

| 风险 | 状态 |
|---|---|
| `release/v0.5`、`v0.5.0-rc.1`、`dev`+14、`feature/v0.6-alpha`、`investigate/*` 均仅存于本地 | **存在**；阶段 0 已建本地 Bundle，未做远程/异机备份 |
| RC metrics 健康窗数据只存在于 HEAD，工作区版本已覆盖 | **已隔离保全**，见阶段 1 |
| 定时任务（schtasks）从工作区分支直接执行脚本，切换分支会影响采集行为 | **存在**；本任务期间按阶段记录，不修改生产 |
| 无 CI，回归只能靠本地全量 pytest | **存在**，P1-1 处理 |

## 5. 原始记录索引

| 文件 | 内容 |
|---|---|
| `raw/git_status.txt` | `git status --short --branch` |
| `raw/branches.txt` | `git branch -avv` |
| `raw/tags.txt` / `raw/tag_rc1.txt` | Tag 清单与附注内容 |
| `raw/remotes.txt` | 远端配置 |
| `raw/graph.txt` | 全 refs 提交图 |
| `raw/worktree_unstaged.diff` | 未暂存改动全文 |
| `raw/worktree_staged.diff` | 暂存区（空） |
| `raw/worktree_status.txt` | porcelain 全量状态（含未跟踪） |
| `raw/branch_unique_commits.txt` | alpha 与 dev 双向独有提交 |
| `raw/untracked.txt` | 未跟踪文件（空） |
| `raw/env.txt` | Python / pytest / 内核版本 |

## 6. 阶段 0 结论

工作区可确认归属：11 个改动全部为实验数据，其中 C_MIN 为合法追加，RC metrics 为覆盖写缺陷产物。没有无法确认归属的源码改动，未执行任何切换分支、重置或清理操作。基线可复现，备份见 `backup_manifest.md`。
