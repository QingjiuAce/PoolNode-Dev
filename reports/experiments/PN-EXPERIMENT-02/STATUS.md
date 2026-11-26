# PN-EXPERIMENT-02 · 交付状态

> 更新：2026-11-27 03:22 CST
> 基线：`fix/experiment-scheduling-guard`

| 阶段 | 内容 | 状态 |
|---|---|---|
| A | 调度冲突收敛（禁用两项 RC） | **PASS** — 见 `reports/scheduling-guard/PN-EXPERIMENT-02-scheduling-{baseline,after}.json` 与 `-scheduling-result.md` |
| B | 12:05 只读最终预检 | **PENDING**（窗口前执行） |
| C | 12:15 由 Task Scheduler 自动执行 | **PENDING** |
| D | 结束后收尾核验 | **PENDING** |
| E | 数据 / 预算 / 归档验收 | **PENDING** |
| F | 后续实验建议 | **PENDING** |

## 当前调度状态（03:22）

```
PoolProbeV05    Ready     （未变）
PoolCminExp     Ready     （12:15 授权窗口，授权未消费）
PoolRCv05       Disabled  ← 本阶段禁用
PoolRCm         Disabled  ← 本阶段禁用
PoolProbeSync   Ready     （未变）
PoolHealthCheck Ready     （未变）
PoolSmokeTest   Ready     （未变，手动）
```

## 阶段 B–F 的固定规则

- 必须由 12:15 的原有触发器启动；不得 `Start-ScheduledTask`、不得直接调 Runner。
- 只读预检；授权/锁/输出绑定任何一项不符 → BLOCKED，不修授权、不改窗口。
- 结束验收覆盖 SUCCESS / PARTIAL / FAILED / 预检失败 / TIMEOUT 五种终态。
- 预算必须区分：计划 71.1 MB / 硬上限 120 000 000 B / 运行时预留 / 实际字节。
- 归档失败记 `ARCHIVE_PENDING`，不检查或修复 SSH。
- 不得重新启用 `PoolRCv05` / `PoolRCm`。
- 结束后 `PoolCminExp` 应为 Disabled 且 `suspended=True`。

## 产物落点

| 文件 | 内容 |
|---|---|
| `preflight.md` | 阶段 B 预检（日期/授权/Runner/矩阵/探针与锁） |
| `execution-summary.md` | 阶段 C 运行摘要与终态 |
| `budget-report.json` | 计划 / 预留 / 实际 / 硬上限 |
| `integrity-report.md` | JSONL、metrics、SHA-256、配对完整性 |
| `archive-report.md` | 归档文件数、大小、SHA、状态 |
| `cleanup-report.md` | 任务禁用、`suspended`、进程与锁 |
| `final-report.md` | 汇总与判定（PASS / CONDITIONAL PASS / PARTIAL / FAIL / BLOCKED） |
| `raw_results/`、`experiment_manifest.json` 等 | 由 `scripts/experiment/postrun_capture.py` 生成 |
