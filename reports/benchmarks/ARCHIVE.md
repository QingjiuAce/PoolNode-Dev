# PoolNode 测量数据归档索引

> 建立：2026-11-25 · 对应 v0.6 任务书 TASK-0.2
> 规则：**raw 层只增不改**；processed/reports 可重算；configs 记录实验条件

## 1. 分层约定

| 层 | 路径 | 规则 |
|---|---|---|
| raw | `reports/benchmarks/*/`（现有 `.jsonl`）| 原始记录，**任何处理脚本不得覆盖或改写**；新增数据追加或开新文件 |
| processed | `reports/benchmarks/processed/` | 清洗/汇总产物，可随时由 raw 重算 |
| reports | `docs/`、`reports/*.md` | 人工审查后的报告与图表 |
| configs | `reports/benchmarks/configs/` | 实验配置、客户端版本、资产 SHA、调度参数 |

## 2. 数据集清单

### 2.1 48h 时间曲线（旧任务，1.7MB smoke）

| 项 | 值 |
|---|---|
| 文件 | `reports/benchmarks/pool-timecurve-20261122-23.jsonl` |
| 轮数 | 27 |
| 覆盖 | 2026-11-22 02:00 → 2026-11-23 23:00 |
| 端点 | 本机（CMCC）→ dl.bszx.site |
| 度量 | 单流 Mbps / 客户端 16 流 Mbps / SHA |
| 客户端 | v0.4-beta（静态等分） |
| 完整性 | 27/27 SHA256 通过 |
| 事件 | 事件 2（22日午后）、事件 3（22日晚）、事件 4（23日晚） |

### 2.2 三臂探针战役（5.4MB bench-6m）

| 项 | 本机 | CN2 盒子（对照） |
|---|---|---|
| 文件 | `reports/benchmarks/v05-timecurve/local-probe-20261123-24.jsonl` | `reports/benchmarks/v05-timecurve/cn2box-probe-20261123-24.jsonl` |
| 轮数 | 27 | 27 |
| 覆盖 | 2026-11-23 02:05 → 2026-11-24 23:05 | 同一时刻表 |
| 度量 | single/w16/w32 聚合 Mbps + 每段耗时 + 尾比 | 同 |
| 完整性 | 81 臂次 SHA 全过 | 81 臂次 SHA 全过 |
| 事件 | 事件 4（23日晚）、事件 5（24日晚） | 零波动（8.97–9.49，端口封顶） |

**合计：81 轮、189 项 SHA256 校验，零错误。**

### 2.3 历史基准（v0.4-beta 晚高峰矩阵）

| 文件 | 内容 |
|---|---|
| `reports/benchmarks/v04-beta-peak/results.jsonl` + `summary.json` | 50 轮，22:48–23:30，5 种流数 × 3 文件 |
| `reports/benchmarks/v04-beta-peak/NOTE.md` | 口径勘误与阅读指南（已修正聚合口径） |
| `reports/benchmarks/v05-pairs/runs.jsonl` + `metrics/` | v0.4 vs v0.5 配对基准（含 cap4 首轮：`runs_cap4_round1.jsonl`） |

### 2.4 持续监控（v0.6，进行中）

| 项 | 值 |
|---|---|
| 本机 | `reports/benchmarks/v06-monitoring/local-probe.jsonl`（新建） |
| CN2 盒子 | `/root/pool-probe/probe-results-v06.jsonl`（盒上，定期回收） |
| 采样时段 | HK 时间 12、16、20、21、22、23 点（每日 6 轮） |
| 调度 | 本机 schtasks `PoolProbeV05`（每小时 :05 触发，守卫过滤）；盒子 cron `5 * * * *`（同守卫） |

## 3. 实验配置记录（configs）

| 项 | 值 |
|---|---|
| 探针脚本 | `scripts/peak_probe.py` @ `47952eb`（+ `--slots` 扩展） |
| 测量资产 | bench-6m：`/pool-tests/bench-6m.jpg`，5,403,720 B，SHA `294d65cc…6359ed` |
| 探针口径 | 单文件三臂顺序执行，顺序按 2h 窗口奇偶交替；聚合吞吐 = 总字节×8÷墙钟 |
| 盒子端口 | 10 Mbps（三臂读数即端口饱和值，不可作为路径容量上限证据） |
| 主机校验 | CN2 盒子 ED25519 `SHA256:ouXt4b//qjBS5IYi0BkSlAYOvGQPMIHZGMdryg8kr+E`（与旧记录一致，strict 校验） |

## 4. 待回收

| 项 | 状态 |
|---|---|
| 盒子 v05 战役数据 | ✅ 已回收进仓库（2.2 表） |
| 盒子 v06 监控数据 | 定期 scp 回收，待建立周期任务 |
| 旧任务 logs | 未入库（非数据记录，仅运行日志） |
