# Phase 3 — 端到端恢复烟测（PN-RECOVERY-01）

> 执行时间：2026-11-27 00:19–00:29 CST
> 方式：**全部经 Windows Task Scheduler 触发**（`Start-ScheduledTask -TaskName PoolSmokeTest`），
> 不是直接调用脚本
> 结论：**通过**——三轮调度、10 项故障注入、真实回传与 SHA 核对均符合预期

## 1. 三轮烟测（同一任务，真实调度器）

| 轮次 | 时间 | cmin | rcv05 | rcmatrix | probe | sync | 说明 |
|---|---|---|---|---|---|---|---|
| R1 | 00:21:47 | **PARTIAL** | SUCCESS | **FAILED** | SUCCESS | **FAILED** | 暴露 3 个真实缺陷 |
| R2 | 00:25:39 | SUCCESS | SUCCESS | SUCCESS | SUCCESS | SUCCESS | 修复后首次全绿 |
| R3 | 00:28:58 | SUCCESS | SUCCESS | SUCCESS | SUCCESS | SUCCESS | 可重复性确认 |

`Get-ScheduledTaskInfo` 在 R2/R3 后 `LastTaskResult=0`（R1 为 1）。

### R1 发现并修复的缺陷（这是烟测的主要价值）

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | cmin `PARTIAL: remote_conflict:runs.jsonl` | 回传保留原文件名，同任务同日期第二次运行与首次冲突 | 上传文件名改为 `<run_id>-<原名>` |
| 2 | rcmatrix `FAILED: validation`（bad_json=0/sha_fail=0） | 校验期望字段 `rep`，而 `rc_matrix.py` 实际写 `round` | keys 改为 `ts/arm/round` |
| 3 | sync `FAILED: remote host and user must be configured explicitly` | 冒烟参数只带 `--out`，丢了 `--host/--user` | smoke_args 补上远端身份 |

三个缺陷都是"配置/命名"级，没有一个是测量逻辑问题——这正是引入统一执行器后
应当被自动发现的那类错误。若沿用旧的 `.ps1` 包装，它们会一直潜伏。

## 2. 各任务成功路径证据（R2/R3）

| 任务 | 新增记录 | 耗时 | SHA 校验 | 回传 |
|---|---:|---:|---|---|
| cmin | 4（single/w8/w13/w16 × 1 rep） | 25.2 s | ok | ok |
| rcv05 | 2（B/D × 1 组） | 35.1 s | ok | ok |
| rcmatrix | 3（A/B/C 各 1 次） | 15.0 s | ok | ok |
| probe | 1（single 臂） | 48.8 s | ok | ok |
| sync | 12（CN2 侧 11-26 积压记录） | 4.2 s | ok | ok |

`sync` 的 12 条是重要副产品：**CN2 探测机在 11-26 一直在采数据，只是回传链断了**——
本次恢复把积压的 12 条拉了回来（写入烟测目录，未混入正式归档）。

## 3. 故障注入（10 项，全部符合设计）

| # | 注入 | 预期 | 实测 |
|---|---|---|---|
| 1 | 脚本路径不存在 | FAILED | `FAILED: script_missing:...\does-not-exist.py` |
| 2 | 解释器路径错误 | FAILED | `FAILED: spawn_failed:FileNotFoundError` |
| 3 | 依赖缺失 | FAILED | `FAILED: dependency_missing:PyYAML:forced-by-injection` |
| 4 | 输出目录不可写 | FAILED | `FAILED: out_dir_not_writable:[WinError 5]` |
| 5 | 网络不可达 | FAILED | `FAILED: origin_unreachable:127.0.0.1:1` |
| 6 | 接收端不可用 | PARTIAL | `PARTIAL: upload:connect_failed:NoValidConnectionsError` |
| 7 | JSONL 格式错误 | FAILED | `FAILED: validation: bad_json=1` |
| 8 | SHA 不一致 | FAILED | `FAILED: validation: sha_fail=1` |
| 9 | 任务超时 | TIMEOUT | `TIMEOUT: timeout after 0.1s` |
| 10 | 同一 run_id 重复触发 | 第二次拒绝 | `first=SUCCESS, second=FAILED: duplicate_run_id` |

**没有任何一项产生伪成功。** 注入只写 `reports/experiment-logs/smoke/`，不触碰正式数据。

## 4. 窗口语义（SKIPPED ≠ SUCCESS）

正式任务 `PoolRCv05` 在 00:25:01 被自己的触发器启动（窗口 20–23 点）：

```
rcv05  SKIPPED  hour 0 not in [20, 21, 22, 23]
```

这是**预期行为**：窗口外不采样，但留下可追溯记录，而不是空跑或假成功。

## 5. 回传与远端核对

| 项 | 结果 |
|---|---|
| 目标 | `/srv/pool/labnode/experiment-inbox/smoke/<task>/<date>/` |
| 文件数 | 41（三轮 × 5 任务：日志 + 状态 + 数据） |
| 体积 | 284 KB |
| 命名 | `<run_id>.log` / `<run_id>.json` / `<run_id>-runs.jsonl` |
| SHA 核对 | 上传后逐文件在远端 `sha256sum` 比对，全部一致 |
| 幂等 | 同名同 SHA 跳过；同名不同 SHA 报 `remote_conflict` |
| 隔离 | 仅在 `labnode` 下，**不在公开根 `/srv/pool/data`** |

样例（远端实际输出）：

```
cfcc09ab…  .../smoke/cmin/2026-11-27/smoke-cmin-20261127T002238-5531fb-runs.jsonl
c2300a3d…  .../smoke/cmin/2026-11-27/smoke-cmin-20261127T002631-75024e-runs.jsonl
```

## 6. 数据隔离声明

| 类别 | 位置 | 是否进入正式数据集 |
|---|---|---|
| 烟测输出 | `reports/experiment-logs/smoke/<run_id>/` | **否** |
| 烟测回传 | `.../experiment-inbox/smoke/` | **否** |
| 正式实验数据 | `reports/benchmarks/{cmin-experiment,v05-rc1,v05-chunk-queue}/` | 本轮**未写入** |
| 历史 11-25 数据 | 同上 | 未修改（`git diff --stat reports/benchmarks/` 为空） |

## 7. 证据文件

| 文件 | 内容 |
|---|---|
| `reports/recovery/phase-3_evidence.json` | 三轮汇总、28 个 run 状态、34 个 smoke JSONL 的 SHA-256 |
| `reports/experiment-logs/smoke/smoke-summary-*.json` | 三轮原始汇总 |
| `reports/experiment-logs/state/<task>/<run_id>.json` | 每次运行的状态 |
| `reports/experiment-logs/<task>/<date>/<run_id>.log` | 每次运行的完整日志 |
| 远端 | `/srv/pool/labnode/experiment-inbox/smoke/` |

## 8. 阶段 3 判定

| 验收项 | 结果 |
|---|---|
| 每个任务一次成功烟测 | ✅ 5/5（R2/R3 各一遍） |
| 每个任务一次失败路径 | ✅ 10 项注入覆盖 |
| 至少两轮完整调度 | ✅ R2 + R3 |
| 一次真实服务器回传 | ✅ 41 文件 |
| 一次本地与远端归档核对 | ✅ 逐文件 SHA |
| 经 Task Scheduler 而非直接调用 | ✅ `PoolSmokeTest` 任务 |
