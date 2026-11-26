# 阶段 0-2 — 任务映射核实（PN-EXPERIMENT-01 §1.2 / R3 收口）

> 核对时间：2026-11-27 01:05–01:20 CST
> 结论：**现行调用链 PASS**；历史包装脚本本体 **UNVERIFIABLE**（已不存在），
> 未发现与实验计划矛盾的证据。

## 1. 映射表（以现行调用链为准）

| Task Scheduler 任务 | runner 参数 | 内部执行函数 | 实际模块 | 实验类型 | 输出目录 |
|---|---|---|---|---|---|
| `PoolCminExp` | `--task cmin` | `execute("cmin")` → `subprocess.run(cmin_experiment.py)` | `scripts/cmin_experiment.py` | C_MIN 96/128 KiB 背靠背配对（1.7 MiB，4 个 worker 组） | `reports/benchmarks/cmin-experiment/runs.jsonl` |
| `PoolRCv05` | `--task rcv05` | `execute("rcv05")` → `bench_v05_pairs.py --arms B,D --pairs 5 --workers 16` | `scripts/bench_v05_pairs.py` | v0.4 static（B）vs v0.5 auto（D）配对 | `reports/benchmarks/v05-rc1/runs.jsonl` |
| `PoolRCm` | `--task rcmatrix` | `execute("rcmatrix")` → `../Pool-investigate/scripts/rc_matrix.py --reps 5 --only-hours 20,21,22,23 --dedupe-hour` | `Pool-investigate/scripts/rc_matrix.py` | v0.5 分块队列矩阵 A(static16)/B(dyn16)/C(dyn21) | `reports/benchmarks/v05-chunk-queue/runs.jsonl` |
| `PoolProbeV05` | `--task probe` | `execute("probe")` → `peak_probe.py --tag local --asset bench-6m --arms single,w16,w32 --slots 12,16,20,21,22,23 --slot-guard` | `scripts/peak_probe.py` | 本机三臂探针（链路表征） | `reports/benchmarks/v06-monitoring/local-probe.jsonl` |
| `PoolProbeSync` | `--task sync` | `execute("sync")` → `collect_box_probe.py --host 160.202.47.187 --user root` | `scripts/collect_box_probe.py` | CN2 探测机结果回收（只增不改） | `reports/benchmarks/v06-monitoring/cn2box-probe.jsonl` |
| `PoolHealthCheck`（新增） | — | `health.py` | `scripts/experiment/health.py` | 任务健康与告警 | `reports/experiment-logs/{alerts*,HEALTH.md}` |
| `PoolSmokeTest`（新增） | `--smoke-all` | `smoke_all()` | 五个任务各跑一次短样本 | 烟测（不写正式数据） | `reports/experiment-logs/smoke/<run_id>/` |

## 2. 证据链（逐层）

### 2.1 计划任务本体（运行中实际配置）

```
PoolRCm  state=Ready  enabled=True  multi=IgnoreNew  user=36833  logon=Interactive
exec : C:\Users\36833\AppData\Local\Python\pythoncore-3.14-64\python.exe
args : -X utf8 "F:\鲸鲸\GitHub\开源二创\Pool-guard\scripts\experiment\runner.py" --task rcmatrix
cwd  : F:\鲸鲸\GitHub\开源二创\Pool-guard
trigger: TimeTrigger start=2026-11-26T00:35:00  Repetition PT1H
```

旧配置（快照 `baseline/task-snapshots/PoolRCm.xml`）：

```
<Command>powershell.exe</Command>
<Arguments>-NoProfile -ExecutionPolicy Bypass -File "C:\Users\36833\AppData\Local\Temp\pool-probe\rcm.ps1"</Arguments>
```

### 2.2 runner 注册表

`scripts/experiment/runner.py` 的 `TASKS["rcmatrix"]`：

```python
"script": "../Pool-investigate/scripts/rc_matrix.py",
"args":   ["--only-hours", "20,21,22,23", "--dedupe-hour", "--reps", "5"],
"hours":  {20, 21, 22, 23},
"out_style": "dir",
"output": "reports/benchmarks/v05-chunk-queue/runs.jsonl",
"keys":   ["ts", "arm", "round"],
```

`--task` 由 `argparse` 限定在 `sorted(TASKS)`；`execute()` 用 `subprocess.run` 调起脚本，
不在此层重写任何测量逻辑。

### 2.3 脚本本体（语义）

`Pool-investigate/scripts/rc_matrix.py`：

```python
URL = 'https://dl.bszx.site/pool-tests/bench-6m.jpg'
SIZE, WORKERS = 5_403_720, 16
ARMS = [('A', 'static', None), ('B', 'dynamic', 16), ('C', 'dynamic', 21)]
```

- A = v0.4 static 16 等分；B = v0.5 dynamic 强制 16 块；C = v0.5 dynamic 21 块。
- 每臂写入 `metrics/<HHMMSS>_<seq>_<arm>.jsonl`，摘要写 `runs.jsonl`。
- 轮内三臂顺序随机（`random.shuffle`），与 `phase-4_experiment_plan.md §2` 一致。
- CLI 支持 `--reps/--only-hours/--dedupe-hour/--force/--out`。

### 2.4 运行证据（本轮实测）

- 烟测 `smoke-rcmatrix-20261127T011455-2e9edd`：3 条记录 + 3 个 metrics 文件
  （`_A/_B/_C` 各一），`sha_ok=true`，回传 ok。
- 正式窗口外触发：`rcmatrix-20261127T003501-3b1d90` → `SKIPPED: hour 0 not in [20, 21, 22, 23]`，
  即"窗口门控 + 不伪成功"链路已生效。

## 3. 历史链接的可验证性

| 项 | 结果 |
|---|---|
| 旧包装脚本 `%TEMP%\pool-probe\rcm.ps1` | **不存在**；Temp 下无 `pool-probe` 目录 |
| Git 历史中的 `.ps1` | 无（恢复阶段已确认） |
| `F:\鲸鲸` 全盘搜索 `v05-chunk-queue` 目录 | **0 个**（仅本轮烟测产生的 smoke 目录） |
| 任何形如 `<HHMMSS>_<seq>_<arm>.jsonl` 的历史 metrics | **0 个**（仅本轮烟测） |
| 事件日志 `Microsoft-Windows-TaskScheduler/Operational` 中 PoolRCm 条目 | 无可用记录 |
| `PoolRCm` 注册时间 | `2026-11-26T00:40:58`（比首次触发 00:35 更晚，说明注册后首轮即为 01:35） |

推论（保守表述）：`PoolRCm` 自注册到采集链中断（11-26 07:15）之间**没有产出任何数据**，
因此不存在"历史 PoolRCm 数据被错误归属"的风险；但旧脚本内容已无法恢复，
"旧任务是否确实调用 `rc_matrix.py`"只剩间接证据。

## 4. R3 风险判定

| 检查 | 判定 |
|---|---|
| 现行 Task → runner → `rc_matrix.py` 调用链 | **PASS**（源码 + 运行中任务 + 实测输出三方一致） |
| 脚本语义与任务书 §7.2 / phase-4 §2 的 A/B/C 定义一致 | **PASS** |
| 历史包装脚本内容 | **UNVERIFIABLE**（文件已不存在，无副本） |
| 是否存在与计划矛盾的证据 | **无** |
| 是否可据此放行 RC 正式实验 | **否**（见 §5） |

按任务书 §1.2「如果 PoolRCm 的真实语义与任务计划不一致，暂停所有 RC 及 A/B/C 安排」：
本轮**未发现**语义不一致，因此不触发该暂停条款；但 §5 的两个新缺陷足以阻止"现在就开跑"。

## 5. 核实过程中发现的两个阻塞级缺陷（已修复）

| # | 缺陷 | 后果 | 修复 |
|---|---|---|---|
| M1 | 正式模式不向子脚本传 `--out`（只有冒烟模式传） | `rc_matrix.py` 用默认值把数据写进 **`Pool-investigate/reports/benchmarks/v05-chunk-queue`**，而 runner 校验的是本仓库路径 → 必然 `missing_output` FAILED，且会写入另一个 worktree（违反"不修改 Pool-investigate"） | `execute()` 统一注入 `--out`（dir 型传目录、file 型传文件） |
| M2 | `rcv05` 同样不传 `--out` | 脚本默认写 `v05-pairs/`，校验读 `v05-rc1/` → 必然 FAILED | 同上；实测烟测 2 条记录写入 `v05-rc1` 校验通过 |

M1/M2 之所以在恢复阶段未被发现，是因为**冒烟模式恰好会传 `--out`**——这是"烟测覆盖不到正式路径"的典型盲区，
本轮用参数化单测（`tests/test_experiment_scheduling.py::test_out_argument_matches_validation_target`）固定住。
