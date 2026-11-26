# PN-EXPERIMENT-02 · 阶段 A 调度冲突收敛结果

> 执行时间：2026-11-27 03:17–03:22 CST（`Asia/Shanghai`，UTC+08:00）
> 执行者：ZCode（本地 Windows，非生产）
> 结论：**PASS** — 仅禁用 `PoolRCv05` / `PoolRCm`，其余五项未变

## 1. 基线（A1，快照见 `PN-EXPERIMENT-02-scheduling-baseline.json`）

| 任务 | TaskPath | State | Enabled | 触发器 | StartWhenAvailable | MultipleInstances | 最近运行 / 结果 |
|---|---|---|---|---|---|---|---|
| PoolProbeV05 | `\` | Ready | True | `:05` 每小时（PT1H，起点 2026-11-23 02:05） | False | IgnoreNew | 03:05:01 / 3（SKIPPED） |
| PoolCminExp | `\` | Ready | True | `:15` 每小时（PT1H，起点 2026-11-25 00:15） | False | IgnoreNew | 03:15:01 / 3（SKIPPED） |
| PoolRCv05 | `\` | Ready | True | `:25` 每小时（PT1H，起点 2026-11-25 00:25） | False | IgnoreNew | 02:25:01 / 3（SKIPPED） |
| PoolRCm | `\` | Ready | True | `:35` 每小时（PT1H，起点 2026-11-26 00:35） | False | IgnoreNew | 02:35:01 / 3（SKIPPED） |
| PoolProbeSync | `\` | Ready | True | 每日 23:40（Daily） | False | IgnoreNew | 11-26 23:40:01 / 4294770688（改指前） |
| PoolHealthCheck | `\` | Ready | True | `:50` 每小时（PT1H） | True | IgnoreNew | 02:50:01 / 1（WARNING） |
| PoolSmokeTest | `\` | Ready | True | 无（手动） | True | IgnoreNew | 01:14:36 / 0 |

七项均唯一匹配（`TaskName` 精确比较 + `TaskPath` = `\`），无重复项；禁用前无 `Running` 实例。

## 2. RC 授权门控检查（A2，只读）

| 项 | rcv05 | rcmatrix |
|---|---|---|
| Runner 入口 | `--task rcv05` → `scripts/bench_v05_pairs.py` | `--task rcmatrix` → `../Pool-investigate/scripts/rc_matrix.py` |
| 窗口门 | `hours = {20,21,22,23}` | 同 |
| `requires_authorization` | **未设置** | **未设置** |
| `suspended` | 未设置 | 未设置 |
| 授权文件 | 无（`authorizations/` 只有 `cmin-2026-11-27T12.json`） | 无 |
| 无授权时的拒绝路径 | **不存在** | **不存在** |

**结论**：这两项任务唯一的门是"小时是否在 20–23"。今晚 20:25 / 20:35 起它们会**真的执行**
（`rcv05` 4 档 × 54 MB ≈ 216 MB，`rcmatrix` 4 档 × 81 MB ≈ 324 MB，合计约 540 MB）。
禁用计划任务是当前唯一能阻止它们的措施；Runner 侧没有授权门可依赖。

## 3. 执行（A3）

```
$m = @(Get-ScheduledTask | ? { $_.TaskName -ceq $name })
if ($m.Count -ne 1) { throw "Task not uniquely identified" }
if ($m[0].State -eq "Running") { 只禁用触发，不杀进程 }
Disable-ScheduledTask -TaskName $m[0].TaskName -TaskPath $m[0].TaskPath
```

- `PoolRCv05` → Disabled（TaskPath `\`）
- `PoolRCm` → Disabled（TaskPath `\`）
- 未删除、未重建、未导入 XML、未改触发器或执行参数。

## 4. 验收（A4）

| 检查 | 结果 |
|---|---|
| `PoolRCv05` | **Disabled**（`enabled=False`） |
| `PoolRCm` | **Disabled**（`enabled=False`） |
| 其余五项 Enabled / Trigger / Settings | **逐字段与基线一致**（差异比对见下） |
| 是否启动过任何实验 | 否（03:05、03:15 两次触发均为窗口外 SKIPPED，rc=3） |
| 是否消费授权 | 否（`.consumed.json` 不存在） |
| 是否新增计划任务 | 否（`Pool*` 仍为 7 个） |
| 是否修改 Git / 远端服务 | 否（工作区仅新增本报告目录） |

差异比对（排除 `lastRunTime/lastTaskResult/nextRunTime` 这类自然变化字段）：

```json
{
  "PoolRCv05": {"state": ["Ready", "Disabled"], "enabled": [true, false]},
  "PoolRCm":   {"state": ["Ready", "Disabled"], "enabled": [true, false]}
}
```

其余五项：**无任何差异**。

## 5. 阶段 A 判定

| 项 | 判定 |
|---|---|
| 两项 RC 任务禁用 | PASS |
| 其余五项未变更 | PASS |
| 未启动实验 / 未消费授权 / 未建任务 | PASS |
| 未触碰 Git 远端与生产 | PASS |

**阶段 A = PASS。**

## 6. 遗留提醒

1. `PoolRCv05` / `PoolRCm` 只是**禁用**，配置与触发器仍在；任何人（或脚本）重新启用它们，
   今晚 20:25 / 20:35 就会立即恢复自动运行——Runner 侧没有授权门兜底。
2. 阶段 B–F（12:05 预检、12:15 自动执行、结束验收）尚未开始；执行入口必须是
   Task Scheduler 的原有触发器，不得 `Start-ScheduledTask` 手动触发。
3. `PoolCminExp` 仍为 Ready，授权未消费；03:15 的触发已证明窗口门有效（hour 3 → SKIPPED）。
