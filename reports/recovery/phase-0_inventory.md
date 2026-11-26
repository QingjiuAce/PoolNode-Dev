# Phase 0 — 环境与资产盘点（PN-RECOVERY-01）

> 执行时间：2026-11-27 00:05–00:18 CST
> 分支：`fix/experiment-pipeline-recovery`（基于 `feature/v0.6-alpha` @ `4cb8b04`）
> 专用工作区：`F:\鲸鲸\GitHub\开源二创\Pool-experiment`
> 性质：只读盘点；唯一写入是任务定义快照（`reports/recovery/task-snapshots/`）

## 1. Git 与工作区

| 工作区 | 分支 | HEAD | 脏文件 |
|---|---|---|---|
| `Pool` | `feature/v0.6-alpha` | `4cb8b04` | 0 |
| `Pool-main` | `main` | `4f3dbad` | 0 |
| `Pool-investigate` | `investigate/v05-chunk-queue` | `98a4e0a` | 0 |
| `Pool-experiment`（本轮新建） | `fix/experiment-pipeline-recovery` | `4cb8b04` | 0 |
| WSL `/home/ZCode/PoolNode` | `test/wsl-debian13-validation` | `a98c4d5` | 0 |
| 云端 `/opt/poolnode-lab/source` | `test/wsl-debian13-validation` | `a98c4d5` | 0 |

依赖：Python 3.14.5（`C:\Users\36833\AppData\Local\Python\pythoncore-3.14-64\python.exe`）、PyYAML 6.0.3、paramiko（回传）。

## 2. 五个计划任务（修改前快照）

快照文件：`reports/recovery/task-snapshots/*.xml`（`Export-ScheduledTask` 原始输出）。

| 任务 | 命令 | 参数 | 工作目录 | 触发 | 上次结果 |
|---|---|---|---|---|:---:|---|
| PoolCminExp | `powershell.exe` | `-NoProfile -ExecutionPolicy Bypass -File "...\Temp\pool-probe\cmin.ps1"` | 未设置 | 每小时，起 11-25 00:15 | `0xFFFD0000` |
| PoolRCv05 | 同上 | `...\rc1.ps1` | 未设置 | 每小时，起 11-25 00:25 | `0xFFFD0000` |
| PoolRCm | 同上 | `...\rcm.ps1` | 未设置 | 每小时，起 11-26 00:35 | `0xFFFD0000` |
| PoolProbeV05 | 同上 | `...\run.ps1` | 未设置 | 每小时，起 11-23 02:05 | `0xFFFD0000` |
| PoolProbeSync | 同上 | `...\collect.ps1` | 未设置 | 每日 23:40 | `0xFFFD0000` |

共同点：账户为当前用户 SID（InteractiveToken，仅登录时运行）、未设工作目录、未设执行时限、无失败重试、**全部指向 `%TEMP%\pool-probe\`**。

## 3. 脚本考古

### 3.1 仓库内（Git 正式版本，优先级 1）

| 文件 | 位置 | 与任务的关系 |
|---|---|---|
| `cmin_experiment.py` | `scripts/`（alpha 与 investigate 均有） | C_MIN 六档测量本体 |
| `bench_v05_pairs.py` | `scripts/` | B/D 配对测量本体 |
| `peak_probe.py` | `scripts/` | 本机三臂探针本体 |
| `collect_box_probe.py` | `scripts/` | CN2 结果回收本体 |
| `rc_matrix.py` | **仅 investigate 分支** `scripts/` | A/B/C 矩阵本体 |

### 3.2 包装层（从未进入 Git）

`.ps1` 五个包装脚本不在任何提交中（`git log --all --diff-filter=A --name-only | grep '\.ps1$'` 只有无关的 `china-speedtest.ps1`）。

### 3.3 磁盘考古结论

| 位置 | 结果 |
|---|---|
| `%TEMP%\pool-probe\` | **不存在**；Temp 下 `pool*` 目录 0 个 |
| 用户目录递归找 `*.ps1`（cmin/rcm/rc1/probe/collect/run） | 仅无关的 `run.ps1`（node_modules） |
| 回收站 | 3 项 / 331 B，无相关文件 |
| `D:\`、`F:\` 递归找 `cmin.ps1` / `pool-probe` | 无 |
| Git 全历史 | 无 |

**结论：包装脚本不可恢复。** 恢复策略采用"Git 正式测量脚本 + 按任务定义重建薄调度层"，并在 §4 逐项核对协议一致性。

## 4. 恢复来源与协议核对

重建的调度参数全部来自**可验证来源**，不凭记忆：

| 任务 | 依据 | 核对结果 |
|---|---|---|
| cmin | `cmin_experiment.py` 默认 `--reps 5 --cmin 98304,131072`；`cmin-experiment/cron.log` 出现 `skip: hour N not in --only-hours` 与 12 点档；`runs.jsonl` 小时分布 01/12/20/21/22/23 | 六档与 `--only-hours 1,12,20,21,22,23 --dedupe-hour` 一致 |
| rcv05 | `bench_v05_pairs.py` 默认 `--pairs 5 --workers 16`；`v05-rc1/runs.jsonl` 20–23 点各 10 臂（5 组 × B/D） | 与 `--arms B,D --pairs 5 --workers 16 --out reports/benchmarks/v05-rc1` 一致 |
| rcmatrix | 任务触发分钟 `:35` 与任务书 §7.2 的 20:35/21:35/22:35/23:35 吻合；`rc_matrix.py` 默认 `--reps 5` 且支持 `--only-hours/--dedupe-hour`；仓库内无其他矩阵输出目录 | **推断**（高置信）：`--only-hours 20,21,22,23 --dedupe-hour --reps 5`；输出 `reports/benchmarks/v05-chunk-queue/` |
| probe | `peak_probe.py` 默认 `--arms single,w16,w32 --slots`；`probe-cron.log` 采样小时 12/16/20/21/22/23，tag=local，资产 bench-6m | 与 `--tag local --asset bench-6m --arms single,w16,w32 --slots 12,16,20,21,22,23 --slot-guard` 一致 |
| sync | `collect_box_probe.py` 默认远端 `/root/pool-probe/probe-results-v06.jsonl`；`collect.log` 显示 23:40 拉取并做 SHA 前缀校验 | 与 `--host/--user` + 每日 23:40 一致 |

**唯一非直接证据**：`PoolRCm → rc_matrix.py` 的映射。依据是触发分钟与任务书四档时间一致，且仓库中不存在第二个"矩阵"脚本。已在报告中标注为推断；`rcmatrix` 任务在预检里会校验脚本存在与 Git 状态，若判断错误会立刻 FAILED 而不是静默跑错。

## 5. 采集链中断原因

| 证据 | 事实 |
|---|---|
| 任务返回码 | 5 个任务均为 `4294770688`（`0xFFFD0000`），持续每小时刷新 |
| 脚本路径 | `%TEMP%\pool-probe\` 不存在 |
| 最后有效日志 | `cmin-experiment/cron.log` 止于 `skip: hour 7`（11-26 07:15）；`v06-monitoring/probe-cron.log` 止于 07:05 |
| 最后有效数据 | `cmin-experiment/runs.jsonl` 11-26 01:15（01:15 档）；探针 jsonl 11-25 23:05 |
| Temp 清理设置 | 未查询到可用的删除事件记录（TaskScheduler Operational 日志无事件） |

**判定：** 可以确认"脚本目录消失导致任务空跑"，**不能**确认消失是系统存储感知清理还是人工删除。两种可能都无法从现有证据排除；本报告不使用"系统清理"作为结论。

## 6. 风险与停止条件核对

| 停止条件 | 状态 |
|---|---|
| 找不到可信历史脚本，且无法确认重建协议一致性 | **未触发**：测量本体全部在 Git；调度参数逐项有日志依据 |
| 不同 worktree 存在无法解释的代码差异 | 未触发 |
| 任务运行可能覆盖原始数据 | 未触发：采集脚本均为只追加；烟测写 `experiment-logs/smoke/` |
| 回传端无法保证幂等 | 未触发：同名同内容跳过、不同内容报错 |
| 任务配置可能影响生产服务 | 未触发：只写 `reports/`，不碰 `/srv/pool/data` |
| 计划任务账户权限超出必要 | 未触发：沿用原当前用户 InteractiveToken，未提权 |
| 实验任务资源竞争 | 需注意：5 个任务都在同一小时的不同分钟（:05/:15/:25/:35/:40），无重叠 |
| 探测点身份/路径无法确认 | 部分：CN2 主机可 SSH（历史成功），广州探针身份仍未核实（不影响本次恢复） |
| 生产服务异常 | 未发现 |
| 无法保证原始数据/Manifest 不被修改 | 未触发：历史 JSONL 只读 |

## 7. 阶段 0 产出

- 任务快照：5 份 XML（可回滚）
- 恢复方案：Git 正式脚本 + 新建 `scripts/experiment/` 薄调度层
- 待验证项：`PoolRCm ↔ rc_matrix.py` 的映射（推断）、sync 的每日窗口
