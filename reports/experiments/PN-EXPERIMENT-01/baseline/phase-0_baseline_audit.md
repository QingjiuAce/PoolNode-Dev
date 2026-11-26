# 阶段 0-1 — 基线审计（PN-EXPERIMENT-01）

> 执行时间：2026-11-27 01:03–01:20 CST（`Asia/Shanghai`，UTC+08:00）
> 执行者：ZCode（本地 Windows，非生产机）
> 判定：**通过（存在 1 项交付路径不一致，已补正；不影响实验基线）**

## 1. 仓库与工作区

| 工作区 | HEAD | 分支 | 工作区状态 |
|---|---|---|---|
| `Pool` | `4cb8b04` | `feature/v0.6-alpha` | 2 个未跟踪目录（见 §2） |
| `Pool-experiment` | `0d1a522` | `fix/experiment-pipeline-recovery` | 干净 |
| `Pool-investigate` | `98a4e0a` | `investigate/v05-chunk-queue` | 干净 |
| `Pool-main` | `4f3dbad` | `main`（= `origin/main`） | 干净 |
| `Pool-guard`（本任务新建） | `e7f6906` | `fix/experiment-scheduling-guard` | 干净（报告提交前） |

- `0d1a522` 是 `d889c18` 的直接子提交（`git log --oneline` 相邻），恢复报告所述关系成立。
- 本任务严格遵守：未切换/重置任何既有分支，未推送、未合并、未移动 tag。
- 唯一新增 tag 相关动作：无。`v0.5.0-rc.1` 仍指向 `0376444`。

## 2. 未提交内容（与恢复报告的差异）

`Pool`（主工作区）存在两个未跟踪目录：

| 路径 | 内容 | 判断 |
|---|---|---|
| `reports/project-state/` | 11-26 项目状态报告 2 份 | 前一任务的交付物，与本任务无关 |
| `reports/recovery/task-snapshots/` | 5 份任务 XML 快照 | **交付路径不一致**：恢复报告把它列为交付物，但未进入 `d889c18/0d1a522`，实际只存在于主工作区 |

处置：把 5 份 XML 原样复制进 `Pool-guard/reports/experiments/PN-EXPERIMENT-01/baseline/task-snapshots/`（新增副本，不移动、不删除原件），并在最终报告中注明。恢复分支本身未改动。

## 3. 历史实验数据未被修改

```
$ git diff --stat reports/benchmarks/     # 空
$ git status --short reports/benchmarks/  # 空
```

历史 JSONL（`cmin-experiment` 200 条、`v05-rc1` 130 条、`v05-pairs` 70 条、
`v06-monitoring` 12 条）字节未变；本任务的全部烟测/故障注入输出都在
`reports/experiment-logs/smoke/` 下，与正式数据隔离。

## 4. 与恢复报告的一致性核对

| 恢复报告陈述 | 实际核对结果 |
|---|---|
| 实现 `d889c18`、报告 `0d1a522` | ✅ 成立 |
| 五任务全部改指向 `runner.py` | ✅ 成立（但 `PoolSmokeTest` 未纳入 `install_tasks.ps1`，见 §5） |
| 新增 `PoolHealthCheck` / `PoolSmokeTest` | ✅ 存在 |
| 41 个文件回传并逐文件 SHA | ✅ 与远端收件箱一致（本轮另做了一次 5 任务烟测，见阶段 1 报告） |
| 268 passed / 1 skipped | ✅ 恢复基线可复现（本轮 guard 分支 293 passed / 1 skipped，+25 为新增用例） |
| 生产 44 文件/字节未变 | ✅ 本轮未触碰生产（也未重启 nginx） |
| 任务快照 5 份为交付物 | ❌ **未入库**，只存在于主工作区未跟踪目录 |
| CN2「12 条 2026-11-26 的历史数据」 | ❌ **不准确**：12 条中 6 条是 11-25、6 条是 11-26（见 CN2 审计报告） |
| `PoolRCm ↔ rc_matrix.py` 为推断（R3） | ⚠️ 部分可确认，历史脚本本体不可恢复（见任务映射报告） |

## 5. 本轮发现并已修正的调度层缺陷

| # | 缺陷 | 影响 | 处置 |
|---|---|---|---|
| D1 | `PoolSmokeTest` 未被 `install_tasks.ps1` 管理 | 改指 worktree 后烟测仍在旧路径执行（实测：01:12 的烟测写进了 `Pool-experiment`） | 已加入安装脚本 |
| D2 | `Get-Command python.exe` 命中 `WindowsApps` 别名 | 计划任务可能调用 Store 占位符而非真实解释器 | 安装时显式 `-Python`，并在报告中固定解释器路径 |
| D3 | `cmin` 的 hours 含 1 点 | 01:15 会自动产生一轮计划外正式实验 | 实测在 01:05:37 禁用任务；hours 改为 `{12,20,21,22,23}` 并加 `suspended` 双保险 |
| D4 | 正式模式不给 dir 型任务传 `--out` | `rcv05`/`rcmatrix` 写一处、校验读另一处；`rcmatrix` 默认输出在 **Pool-investigate** worktree | 统一由 `execute()` 注入 `--out`（见阶段 1 报告） |
| D5 | CN2 归档 CRLF、远端 LF | 正式 sync 必然 `remote file diverges` | 采集器改为按行归一化比较，仍只追加远端尾部字节 |

## 6. 阶段判定

| 检查项 | 结果 |
|---|---|
| 当前 HEAD 与报告一致 | PASS |
| 工作区无覆盖风险 | PASS（未跟踪文件已识别，只做新增副本） |
| 未提交实验数据 | PASS（无） |
| 历史数据未被改动 | PASS |
| 分支/tag 未被移动 | PASS |
| 交付路径一致性 | PARTIAL（1 项，已补正） |
