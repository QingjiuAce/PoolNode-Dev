# V0.5 策略对比复测计划（2026-11-26）

> 状态：**计划已就绪，尚未执行**。本文件不包含任何实测结论。
> 前置：Harness 策略修复已完成（B=static，D=auto，运行后以 metrics 校验）。
> 约束：真实网络复测需要独立授权；不得与探针、C_MIN、队列矩阵并发。

## 1. 目标

用**真正的 static vs auto** 配对回答两个问题：

1. 健康窗：v0.5 auto 相对 v0.4 static 的退化是否 ≤10%（中位）/ ≤15%（P90）？
2. 劣化窗：v0.5 auto 相对 v0.4 static 的提升是否 ≥30%，且 ≥4/5 组为正？

只有两项都满足，V0.5 才重新具备验收条件；否则维持 RC 候选状态。

## 2. 固定条件

| 项 | 值 |
|---|---|
| 资产 | `bench-6m`（5,403,720 B，SHA `294d65cc…`） |
| 并发 | `--workers 16` |
| 臂 | `--arms B,D`（B 强制 static；D 强制 auto） |
| 配对数 | 每窗口 **5 组**（10 臂） |
| 轮内顺序 | Harness 逐轮反转，B/D 平衡 |
| 间隔 | `--gap 2`（秒） |
| 输出 | 每窗口独立目录；`runs.jsonl` 追加；`metrics/<run_id>.<seq>.jsonl` |
| 完整性 | 每臂 SHA256；`policy_ok=true` 才计入 |
| 并发防护 | `runs.jsonl.lock`；冲突输出 `SKIPPED_OVERLAP`，该次不计入 |

## 3. 命令

### 3.1 健康窗（择稳定时段，单流 ≥15 Mbps 且无事件）

```bash
python scripts/bench_v05_pairs.py \
  --pairs 5 --arms B,D --scheduler auto \
  --asset bench-6m --workers 16 \
  --assets config/bench-assets.yaml \
  --out reports/benchmarks/v05-rc1-retest/healthy-20261127 \
  --gap 2
```

### 3.2 劣化窗（20–23 点，事件正在发生时）

```bash
python scripts/bench_v05_pairs.py \
  --pairs 5 --arms B,D --scheduler auto \
  --asset bench-6m --workers 16 \
  --assets config/bench-assets.yaml \
  --out reports/benchmarks/v05-rc1-retest/event-20261127-20 \
  --gap 2
```

每个劣化时段（20/21/22/23）单独一个输出目录；四个时段全部保留，不合并成一次“平均”。

### 3.3 同期对照（必须）

- 本机单流探针：`python scripts/peak_probe.py --asset bench-6m --arms single --tag local --out <同目录>/probe.jsonl --force`
- CN2 盒子探针：由 `PoolProbeSync` 23:40 回收，或手动 `peak_probe.py` 同参数。
- 记录到 `conditions.md`：开始/结束时间、单流 Mbps、CN2 盒子 Mbps、是否处于事件中。

## 4. 判定方法

1. 剔除 `policy_ok=false`、`sha_ok=false`、`rc!=0` 的臂。
2. 按 `round` 配对 B/D，计算 `delta = (D_wall − B_wall) / B_wall`。
3. 健康窗：`median(delta) ≤ 10%` 且 `P90(delta) ≤ 15%`。
4. 劣化窗：`median(delta) ≥ 30%` 且 5 组中 ≥4 组 `delta > 0`。
5. 报告必须同时给出 `actual_scheduler` 分布（B 全 static、D 全 dynamic），
   否则视为 Harness 失败，不得进入判定。

## 5. 记录与防伪

- 每臂 metrics 文件唯一，含 `run_id`；`runs.jsonl` 追加不覆盖。
- 原始数据只读；分析脚本另存 processed 结果。
- 运行时间、网络状态、是否重叠均写入 `conditions.md`；补填或推测的数据不得写入。
- 若某时段因事件不足（单流仍 >15 Mbps）而不构成劣化窗，标记 `NOT_DEGRADED`，
  不计入劣化判定，也不得挪用到其他时段。

## 6. 当前状态

| 项 | 状态 |
|---|---|
| Harness 修复 | ✅ 完成 |
| 本地策略测试 | ✅ 13 项通过 |
| 健康窗复测 | ⬜ 未执行 |
| 劣化窗复测 | ⬜ 未执行 |
| V0.5 重新验收 | ⬜ 不具备条件（缺真实数据） |
