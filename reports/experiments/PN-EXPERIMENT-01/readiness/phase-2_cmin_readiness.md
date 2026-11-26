# 阶段 2/3 — C_MIN 首轮正式实验准备与授权申请（PN-EXPERIMENT-01 §3–§4）

> 编制时间：2026-11-27 01:30 CST
> 代码基线：`fix/experiment-scheduling-guard` @ `e7f6906`（实验脚本与测量逻辑未改）
> 状态：**准备完成，等待窗口授权**。未产生任何正式 C_MIN 流量。

## 1. 实际执行的协议（与任务书逐条对应）

| 任务书要求 | 实现位置 | 实际行为 |
|---|---|---|
| 每窗口五组配对 | `cmin_experiment.build_order(reps=5)` | 5 个 rep；每个 rep 覆盖 4 个 worker 组（single/w8/w13/w16），每组内 96K 与 128K **背靠背**执行 |
| 组内顺序随机 | `rng.shuffle(pair)` | 同一 worker 组的两个 C_MIN 值顺序随机 |
| 组间顺序随机 | `rng.shuffle(groups)` | 每个 rep 的 4 个 worker 组顺序随机 |
| 记录随机种子 | 本轮新增：`seed = secrets.randbits(32)`，写入每条记录 | 可用 `random.Random(seed)` 完整复现顺序 |
| 每组独立标识 | `arm_id = run_id:r<rep>:<arm>:c<cmin>` | 40 条唯一 |
| 每个运行实例独立 run_id | `new_run_id()` | 由 runner 生成并写入每条记录 |
| 不改算法参数/文件/worker/阈值 | — | 本轮未改 `cmin_experiment.py` 的测量逻辑（只加 `seed`/`seq` 两个元数据字段） |

**术语澄清**（避免验收歧义）：任务书说"五组配对，每组含 96K/128K"，
而脚本的"组"是 worker 组。一个窗口的真实产出是：

```
5 reps × 4 worker 组 × 2 C_MIN = 40 次下载 = 20 个背靠背配对
```

历史窗口（11-25 12/20/21/22/23 点）只有 24 条 = 3 reps = 12 个配对，
说明旧调度用的是 `--reps 3`（或中途被杀；记录里没有 run_id，无法区分）。
本轮按 5 reps 执行，与 phase-4 计划里 "2 C_MIN × 4 臂 × 5 组 × 1.7 MB ≈ 68 MB" 的算式一致。

判定口径：**"五组 96K/128K 配对完整" = 5 个 rep 全齐且每个 rep 内 4 个 worker 组
都出现两种 C_MIN**（40 条记录、20 对），不是"总共 5 对"。

## 2. 输入与输出

| 项 | 值 |
|---|---|
| 资产 | `https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg` |
| 大小 / SHA-256 | 1 778 514 B / `b6043507…aa932e` |
| C_MIN 档位 | 98 304 B（96 KiB）、131 072 B（128 KiB） |
| worker 组 | 1 / 8 / 13 / 16（受 `size // cmin` 约束：128K 时 w16 实际只切 13 块） |
| 正式输出 | `reports/benchmarks/cmin-experiment/runs.jsonl`（只追加） |
| 每次运行隔离 | `reports/experiment-logs/cmin/<date>/<run_id>.log`、`state/cmin/<run_id>.json` |
| 回传 | `/srv/pool/labnode/experiment-inbox/cmin/<date>/<run_id>-runs.jsonl` 等 3 个文件 |

## 3. 遥测字段（实际保留）

单条记录字段（`cmin_experiment.py` + `peak_probe.run_ranges`）：

| 字段 | 含义 |
|---|---|
| `run_id / seed / seq / arm_id` | 运行标识、随机种子、实际执行序号、配对标识 |
| `ts / epoch / slot_kind / rep / cmin / workers / arm` | 时间、档位、轮次、配置 |
| `chunks / chunk_bytes / segment_bytes` | 实际切块数、块大小 |
| `wall_ms / mbps / requests / retries` | 总耗时、聚合吞吐、请求数、重试数 |
| `segment_ms / seg_mbps{min,median,max} / slowest_ms / tail_ratio` | 每段耗时（完成顺序）、分段吞吐分布、最慢段、尾部比 |
| `sha_ok / status / error` | 完整文件 SHA-256 校验、状态、错误 |

关于 `spans`：当前 `run_ranges` 不把字节区间写进记录，但区间是
**纯确定性函数**，可由记录里的 `size` 与 `chunks` 精确重建：

```python
spans = [(size*i//n, size*(i+1)//n - 1) for i in range(n)]   # n = chunks
```

因此"块区间"没有丢失；`segment_ms` 是**完成顺序**的列表，不保证与 span 下标对齐，
这一点会在分析中如实说明（不使用"第 i 段 = 第 i 个 span"的错误映射）。
若后续需要严格的 span↔耗时对应，应另开任务修改测量脚本（本轮未改）。

每个窗口另有：
- `reports/experiment-logs/telemetry/<run_id>.json`：DNS / TCP / TLS / TTFB / 1 字节 Range / ICMP（origin 与对照目标）；
- 同小时 `:05` 的本机探针记录（`local-probe.jsonl`）与 CN2 探针记录（`cn2box-probe.jsonl`，独立链路）。

## 4. 流量预算

| 项 | 计算 | 流量 |
|---|---|---:|
| 单窗口 40 次下载 | 40 × 1 778 514 B | **71.1 MB**（67.9 MiB） |
| 链路遥测 | 1 次 DNS/TCP/TLS/HEAD/1B GET + 8 个 ICMP | < 10 KB |
| 回传 | 日志 + 状态 + runs.jsonl（新增 40 行） | ≈ 200 KB |
| 失败重试上限 | 脚本内每段最多 2 次尝试（仅分段臂） | ≤ 理论 +100% |
| **建议批准上限** | 含最坏重试与二次尝试 | **120 MB / 窗口** |

自动重试不会无限增长：`run_ranges` 每段最多 2 次尝试，`single` 臂不重试；
runner 不做自动补跑；窗口内 `--dedupe-hour` 保证同一小时只跑一次。

## 5. 窗口分类方案（§4.2）

预先固定的阈值（写在 `window_class.py` 顶部，跑之前声明）：

| 判据 | 阈值 |
|---|---|
| 吞吐（本机探针，窗口 ±90 min 内所有臂的中位数） | ≥ 5 Mbps 为健康 |
| origin 丢包（窗口内遥测 ICMP） | ≥ 20% 视为链路劣化 |
| origin TTFB（窗口内遥测 HEAD） | ≥ 3000 ms 视为链路劣化 |

判定：

| 组合 | 结果 |
|---|---|
| 吞吐 ≥ 5 Mbps 且 无链路劣化 | `HEALTHY` |
| 吞吐 < 5 Mbps 且 无链路劣化 | `DEGRADED` |
| 吞吐 < 5 Mbps 且 有链路劣化 | `DEGRADED` |
| 吞吐 ≥ 5 Mbps 但 有链路劣化 | `UNKNOWN`（证据冲突，不下结论） |
| 无 ±90 min 探针样本 | `UNKNOWN` |
| 本窗口 `HEALTHY` 且此前 12 h 内出现过 `DEGRADED` 档位 | `EVENT_TAIL` |

`UNKNOWN` 是合法结果：数据保留，但**不用于健康/劣化窗口的算法结论**。
不会因为"计划需要一个健康窗"而把证据不足的窗口标成 HEALTHY。

## 6. 启动前预检清单（§4.1）

| # | 检查 | 现在可确认 | 窗口时确认方式 |
|---|---|---|---|
| 1 | `PoolHealthCheck` 通过 | ✅ 01:18 总体 OK（cmin=SUSPENDED） | 运行前再跑一次 |
| 2 | 相关探针在线 | ✅ CN2 12/12 记录、时间同步；本机 probe 可运行 | `--task probe` 的最近记录 |
| 3 | 探针时间同步 | ✅ CN2 `System clock synchronized: yes`；本机 +08:00 | 同上 |
| 4 | 香港节点状态正常 | ✅ 烟测 5/5 下载 SHA 全对 | 遥测 TTFB/状态码 |
| 5 | 数据盘挂载正常 | —（实验只写本机 F:；生产盘不在本任务范围） | 不适用 |
| 6 | 输出目录可写 | ✅ 预检 `.write-test-*` 已通过（烟测） | 预检自动执行 |
| 7 | 实验文件与参数校验 | ✅ 资产 size/SHA 与记录一致 | 预检 TLS + 下载 SHA |
| 8 | runner 版本与 commit 固定 | ✅ `e7f6906`；新增 `code_dirty` 门（正式模式代码有未提交改动 → FAILED） | 运行前 `git status` |
| 9 | run_id 唯一性 | ✅ `时间戳+6 hex`，重复则 `duplicate_run_id` FAILED | 状态目录检查 |
| 10 | 资源锁可获取 | ✅ 烟测与双进程测试 | 运行时日志 `locks` |
| 11 | 无冲突中的正式实验 | ✅ 当前无正式实验（cmin 挂起） | 锁冲突会记 SKIPPED |
| 12 | 无未完成的 sync | ⚠️ CN2 有 6 条待并入（非 in-flight；下次 23:40 自动补） | 不阻塞 C_MIN |
| 13 | 远端 Inbox 可用 | ✅ 本轮 3 次上传均 ok | 回传结果 |

## 7. 授权申请（§4.3）

| 项 | 建议值 |
|---|---|
| 实验日期 | **2026-11-27（今天）** |
| 机器本地时区 | `Asia/Shanghai`，UTC+08:00（`tzutil` = China Standard Time） |
| 窗口 | **W1 `12:15`**（本机 12:15 触发，计划任务 `PoolCminExp`） |
| 对应 UTC | 2026-11-27 **04:15 UTC** |
| 流量上限 | **120 MB**（预计 71.1 MB + 遥测/回传/重试） |
| 探针与链路 | 本机家宽 → `dl.bszx.site`（香港 156.233.227.20）；CN2 `160.202.47.187` 作独立参照 |
| 分支 / commit | `fix/experiment-scheduling-guard` @ `e7f6906`（报告提交后会给出最终 commit） |
| 启动方式 | `Enable-ScheduledTask -TaskName PoolCminExp`，由 `:15` 触发器启动；**不**直接调用脚本 |
| 解除挂起 | 需同时把 `runner.TASKS["cmin"]["suspended"]` 改为 `False` 并提交（一个字段） |
| 剩余风险 | §8 |

若今天 12:15 不合适，可指定 20:15 / 21:15 / 22:15 / 23:15 中的任意一档，或改日期；
**未获明确确认前不会启用任务，也不会改 `suspended` 标志。**

## 8. 本轮仍存在的风险（跑之前需要接受）

| # | 风险 | 影响 | 处置 |
|---|---|---|---|
| A1 | 笔记本电池策略 | 未接电源/合盖 → 整档不运行 | 窗口前确认已接电源、不休眠 |
| A2 | 无外部告警 | 失败需人工查 `HEALTH.md` | 窗口后 10 分钟内人工检查状态 JSON |
| A3 | 同分钟双探针 | 分类证据可能略偏低 | 以窗口内遥测为主；必要时标 UNKNOWN |
| A4 | 劣化窗可能超时 | 单流塌到 0.05 MB/s 时 40 次下载可能逼近 30 min 超时 | 超时记 `TIMEOUT`，已产生的记录保留为 `PARTIAL`，不补造 |
| A5 | 历史窗口只有 3 组 | 与历史对比时口径不同（3 vs 5 组） | 分析时按 rep 分组，不与历史混算 |

## 9. 本轮不做

- 不跑其余四个窗口、不做 A/B/C、不做 RC 正式实验；
- 不修改 v0.5 算法、不改生产 auto 计划、不碰生产数据与 nginx；
- 不把 11-26 的 CN2 积压数据混入 C_MIN 数据集；
- 不解除 v0.6 Alpha 冻结。
