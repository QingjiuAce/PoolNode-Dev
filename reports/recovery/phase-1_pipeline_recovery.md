# Phase 1 — 采集脚本与任务入口恢复（PN-RECOVERY-01）

> 执行时间：2026-11-27 00:15–00:26 CST
> 分支：`fix/experiment-pipeline-recovery` @ 专用工作区 `Pool-experiment`
> 结论：五个任务已全部指向持久化入口，**不再依赖 `%TEMP%`**

## 1. 目录（已纳入 Git）

```
scripts/experiment/
├── README.md            # 使用说明与约束
├── runner.py            # 唯一入口：--task / --smoke / --smoke-all / --inject
├── health.py            # 心跳、连续失败、错过窗口检测
└── install_tasks.ps1    # 重指任务（ASCII-only，兼容 PowerShell 5.1）
```

运行期产物（不入 Git）：

```
reports/experiment-logs/
├── <task>/<YYYY-MM-DD>/<run_id>.log
├── state/<task>/<run_id>.json
├── smoke/<run_id>/...
├── alerts.current.json
├── alerts.jsonl
└── HEALTH.md
```

## 2. 任务重指（旧 → 新）

| 任务 | 旧入口（已失效） | 新入口 | 触发器保留 |
|---|---|---|---|
| PoolCminExp | `%TEMP%\pool-probe\cmin.ps1` | `python.exe -X utf8 <repo>\scripts\experiment\runner.py --task cmin` | 每小时 :15 |
| PoolRCv05 | `%TEMP%\pool-probe\rc1.ps1` | `… --task rcv05` | 每小时 :25 |
| PoolRCm | `%TEMP%\pool-probe\rcm.ps1` | `… --task rcmatrix` | 每小时 :35 |
| PoolProbeV05 | `%TEMP%\pool-probe\run.ps1` | `… --task probe` | 每小时 :05 |
| PoolProbeSync | `%TEMP%\pool-probe\collect.ps1` | `… --task sync` | 每日 23:40 |
| **PoolHealthCheck（新增）** | — | `…\health.py` | 每小时 :50 |
| **PoolSmokeTest（新增）** | — | `… --smoke-all` | 无触发器（手动） |

`Set-ScheduledTask -Action` 只替换动作，**触发器、账户（当前用户 InteractiveToken）、启用状态全部保留**；原 XML 已快照到 `reports/recovery/task-snapshots/`，可一键回滚。

工作目录显式设为仓库根（旧任务未设工作目录），解释器为绝对路径 `C:\Users\36833\AppData\Local\Python\pythoncore-3.14-64\python.exe`。

## 3. runner 的职责边界

| 做 | 不做 |
|---|---|
| 窗口判断、预检、日志、状态、结果校验、回传 | 不实现任何测量逻辑 |
| 统一退出码（0/1/3/4/5） | 不改历史 JSONL、不覆盖正式数据 |
| 按 run_id 隔离的日志与状态 | 不写 `/srv/pool/data`（生产公开目录） |
| 冒烟写 `smoke/<run_id>/` | 不把烟测数据混进正式数据集 |

五个任务的测量本体仍来自 Git：

| 任务 | 测量脚本 | 位置 |
|---|---|---|
| cmin | `cmin_experiment.py` | `scripts/`（alpha） |
| rcv05 | `bench_v05_pairs.py` | `scripts/`（alpha） |
| rcmatrix | `rc_matrix.py` | `scripts/`（**investigate 分支 worktree**，按路径引用，不复制） |
| probe | `peak_probe.py` | `scripts/`（alpha） |
| sync | `collect_box_probe.py` | `scripts/`（alpha） |

`rcmatrix` 引用 `../Pool-investigate/scripts/rc_matrix.py`；若该 worktree 缺失，预检直接 FAILED，绝不静默跳过。

## 4. 对正式脚本的唯一改动

`scripts/collect_box_probe.py`：新增 `--out` / `--log`（默认值不变），使 CN2 回收可以在烟测中重定向输出而不污染正式归档。改动向后兼容，既有调用方式不受影响。

## 5. 退出码与状态

| 状态 | 退出码 | 触发条件 |
|---|---:|---|
| SUCCESS | 0 | 执行成功、记录校验通过、回传成功（或明确 skipped） |
| FAILED | 1 | 预检失败 / 子进程非零 / 校验失败 / 窗口内无记录 |
| SKIPPED | 3 | 窗口外，或子进程自身输出 `skip:` |
| PARTIAL | 4 | 本地成功但回传失败 |
| TIMEOUT | 5 | 超过任务时限 |

**SKIPPED 永不写成 SUCCESS**：窗口外触发（如 PoolRCv05 在 00:25）会被如实记录为
`SKIPPED: hour 0 not in [20, 21, 22, 23]`，实测见 phase-3。

## 6. 幂等与覆盖保护

- `run_id` 由「任务名 + 时间戳 + 6 位随机」组成；同 `run_id` 重放 → 第二次直接 `FAILED: duplicate_run_id`（实测通过）。
- 回传按 `run_id` 命名文件；远端同名同 SHA 跳过、同名不同 SHA 报 `remote_conflict` 并置 PARTIAL（实测发现并已修复命名策略）。
- 正式数据文件只追加；`collect_box_probe` 自身还要求远端文件是本地的前缀扩展。

## 7. 阶段 1 交付

| 项 | 路径 |
|---|---|
| 入口 | `scripts/experiment/runner.py` |
| 健康检查 | `scripts/experiment/health.py` |
| 任务安装 | `scripts/experiment/install_tasks.ps1` |
| 说明 | `scripts/experiment/README.md` |
| 任务快照 | `reports/recovery/task-snapshots/*.xml` |
