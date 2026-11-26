# PN-EXPERIMENT-01 最终报告（阶段 0–3 完成，阶段 4 待授权）

> 执行时间：2026-11-27 01:03–01:55 CST（`Asia/Shanghai`，UTC+08:00）
> 分支：`fix/experiment-scheduling-guard`（独立 worktree `Pool-guard`，基于恢复基线 `0d1a522`）
> 提交：`e7f6906`（实现）、`f4065f8`（阶段 0–1 报告）
> 环境：Windows 11 家庭版中文版，16 逻辑核 / 31.1 GB，Python 3.14.5，Git Bash

---

## 一、执行摘要

阶段 0–3 **全部完成**；**阶段 4（首轮正式 C_MIN 窗口）未执行**——任务书 §0.3 要求
明确日期、时区、窗口与流量上限后才能启动，本次任务发起时未提供这些信息，
因此按规程停在"提交首轮运行申请"。

在准备过程中发现并修复了 **6 个会直接影响数据可信度的缺陷**（3 个来自恢复阶段的遗留，
3 个在本次核实中发现），其中两个是"正式模式必然失败"级别：

| # | 缺陷 | 级别 | 状态 |
|---|---|---|---|
| D1 | 正式模式不给 `rcv05`/`rcmatrix` 传 `--out` → 写一处、校验另一处；`rcmatrix` 会写进 `Pool-investigate` worktree | 阻塞 | 已修复 + 回归测试 |
| D2 | CN2 正式归档是 CRLF、远端是 LF → 正式 sync 必然 `remote file diverges` | 阻塞 | 已修复（只追加尾部，历史字节不改） |
| D3 | `cmin` hours 含 1 点 → 01:15 自动产生计划外正式实验 | 高 | 01:05:37 禁用任务 + 代码挂起 + 窗口修正 |
| D4 | 全部任务共用同一家宽链路，却只有"错开 10 分钟"的假设 | 高 | 已加跨进程互斥锁 |
| D5 | `PoolSmokeTest` 未纳入安装脚本（实测烟测跑进了旧 worktree） | 中 | 已修复 |
| D6 | 正式运行不校验代码是否已提交（commit 无法标识实际代码） | 中 | 已加 `code_dirty` 门 |

## 二、Git 与环境

| 项 | 值 |
|---|---|
| 本任务分支 | `fix/experiment-scheduling-guard` |
| 实现提交 | `e7f6906` |
| 报告提交 | `f4065f8` |
| 基线（恢复） | `0d1a522`（未改动） |
| 本任务 worktree | `F:\鲸鲸\GitHub\开源二创\Pool-guard` |
| 其他 worktree | `Pool`=`4cb8b04`、`Pool-experiment`=`0d1a522`、`Pool-investigate`=`98a4e0a`、`Pool-main`=`4f3dbad`，全部未切换/未改动 |
| tag | `v0.5.0-rc.1` 仍指向 `0376444`，未移动 |
| 时区 | `Asia/Shanghai`（`tzutil` = China Standard Time），UTC+08:00 |
| 解释器 | `C:\Users\36833\AppData\Local\Python\pythoncore-3.14-64\python.exe` |

未推送、未合并、未 reset/clean、未删除任何文件。

## 三、任务映射结果（R3 收口）

| Task | runner 参数 | 实际模块 | 实验类型 | 输出 |
|---|---|---|---|---|
| PoolCminExp | `--task cmin` | `scripts/cmin_experiment.py` | C_MIN 96/128 KiB 配对 | `reports/benchmarks/cmin-experiment/runs.jsonl` |
| PoolRCv05 | `--task rcv05` | `scripts/bench_v05_pairs.py` | v0.4 static(B) vs v0.5 auto(D) | `reports/benchmarks/v05-rc1/runs.jsonl` |
| PoolRCm | `--task rcmatrix` | `Pool-investigate/scripts/rc_matrix.py` | 分块队列 A/B/C | `reports/benchmarks/v05-chunk-queue/runs.jsonl` |
| PoolProbeV05 | `--task probe` | `scripts/peak_probe.py` | 三臂探针 | `reports/benchmarks/v06-monitoring/local-probe.jsonl` |
| PoolProbeSync | `--task sync` | `scripts/collect_box_probe.py` | CN2 回收 | `reports/benchmarks/v06-monitoring/cn2box-probe.jsonl` |

- 现行调用链（Task XML → runner → 脚本 → 输出）**三方一致**，并有运行证据。
- 历史包装脚本 `%TEMP%\pool-probe\rcm.ps1` 已不存在，Git 与全盘均无副本，
  `rc_matrix.py` 的语义（A/B/C、20–23 点、`--reps 5`）与计划一致且无相反证据。
- `PoolRCm` 自注册（11-26 00:40）至中断（07:15）**零产出**：全盘无 `v05-chunk-queue`
  目录、无历史 metrics 文件——因此不存在历史数据被错误归属的风险。
- 结论：R3 = **PASS（现行链）**，历史脚本本体 **UNVERIFIABLE**（如实记录，不推断）。

详见 `baseline/phase-0_task_mapping.md`。

## 四、调度与资源竞争

- 实际触发器已逐条读取（含时区、账户、多实例策略、上次结果），并纠正了 cmin 的 1 点档。
- 资源矩阵：本机家宽下行/上行、CN2 SSH、CN2/广州探针（跨机器、无法加锁）、
  本机 CPU/内存/临时文件、回传通道。
- 关键冲突判定：`:15` cmin 与 `:25` rcv05 在劣化窗只余约 2.4 min 余量 →
  已用锁互斥，冲突记 `SKIPPED`；`23:35` rcmatrix 可能压到 `23:40` sync →
  sync 记 SKIPPED，次日自动补齐；`:50` 健康检查不取锁、无流量。
- 实测耗时：烟测 cmin 6.8 s / rcv05 11.4 s / rcmatrix 7.1 s / probe 18.3 s / sync 5.6 s；
  历史 cmin 5 组外推 22 s（12 点）–457 s（23 点）。

详见 `scheduling/phase-1_scheduling_review.md`。

## 五、互斥机制

| 要求 | 实现 |
|---|---|
| 同一链路互斥 | `link:local-hk` 独占 |
| 同一输出目录互斥 | `out:<dir>` 独占 |
| Sync 不与实验争用 | sync 取 `link:local-hk` + `ssh:cn2` |
| 手动 = 计划任务 | 同一 `execute()`；锁目录机器级 `%LOCALAPPDATA%\PoolNode\locks` |
| 冲突留痕 | `locks.jsonl`（acquired/conflict/released）+ 状态 JSON |
| 冲突不算样本 | `SKIPPED`、退出码 3、`record_count=0` |
| 不排队 | 纯非阻塞（`LK_NBLCK` / `LOCK_NB`） |
| 无死锁/残留 | 排序获取；内核在进程死亡时释放，无年龄启发式 |

验收：26 个新单测（含异常退出释放、部分获取回滚、陈旧元数据不阻塞），
全量 **294 passed / 1 skipped**（基线 268/1，+26 全部为新增）；
真实双进程实测 `cmin` 被 `probe` 拒绝并记 `SKIPPED: lock_conflict: ... pid=44928`；
`PoolSmokeTest` 经 Task Scheduler 触发 5/5 SUCCESS，锁全部成对 acquired/released。

详见 `scheduling/phase-1_isolation_implementation.md`。

## 六、CN2 历史积压核验

| 项 | 结果 |
|---|---|
| 来源 | `160.202.47.187`（`ser654121548140`），`Asia/Shanghai`，时钟已同步 |
| 远端文件 SHA-256 | `0cc764ff…ab494`，与拉取载荷**逐字节一致** |
| 记录数 | 12（6 条 11-25 已在归档 + 6 条 11-26 新增） |
| 完整性 | 12 行合法 JSON、无半行、`(ts,tag,asset)` 唯一、36/36 臂 `sha_ok` |
| 恢复报告更正 | "12 条 11-26" → 实为 6+6 |
| 独立归档 | `cn2-backlog/records/cn2-probe-backlog-20261127.jsonl` + `manifest.json` |
| 是否并入正式归档 | **否**（本轮不执行正式同步） |
| 旁证 | 12 档 CN2 吞吐 8.36–9.49 Mbps，说明 11-26 晚间的塌陷是本机侧而非服务端 |

详见 `cn2-backlog/phase-0_cn2_backlog_audit.md`。

## 七、首轮 C_MIN 窗口：未执行

| 项 | 状态 |
|---|---|
| 协议已核实 | ✅ 5 reps × 4 worker 组 × 2 C_MIN = 40 次下载 / 20 对，种子与执行序号入库 |
| 预算 | 71.1 MB / 窗口（建议批准上限 120 MB） |
| 预检 | ✅ `precheck(formal)` 返回空；锁可获取；`code_dirty=[]` |
| 授权 | ❌ **未提供**（日期/窗口/流量上限） |
| 实际流量 | **0**（cmin 任务自 01:05:37 起禁用；`suspended` 双保险） |

### 授权申请（待确认）

| 项 | 建议值 |
|---|---|
| 日期 | 2026-11-27（今天） |
| 时区 | `Asia/Shanghai`（UTC+08:00） |
| 窗口 | W1 **12:15**（= 04:15 UTC） |
| 流量上限 | 120 MB |
| 链路 | 本机家宽 → `dl.bszx.site`；CN2 作独立参照 |
| 启动 | `Enable-ScheduledTask PoolCminExp`，由 `:15` 触发器启动 |
| 解除挂起 | 改 `TASKS["cmin"]["suspended"]=False` 一个字段并提交 |

备选：20:15 / 21:15 / 22:15 / 23:15 任一档，或改日期。未获确认前不会启用。

## 八、原始数据与 SHA

| 类别 | 路径 | 校验 |
|---|---|---|
| 本轮烟测数据 | `reports/experiment-logs/smoke/<run_id>/runs.jsonl` | 每条 `sha_ok`；上传逐文件 SHA |
| 本轮状态 | `reports/experiment-logs/state/<task>/<run_id>.json` | 含 `locks`、`code_dirty` |
| 锁日志 | `reports/experiment-logs/locks.jsonl` | 与正式数据分离 |
| CN2 积压 | `cn2-backlog/records/*.jsonl` | 清单内逐行 SHA-256 |
| 历史正式数据 | `reports/benchmarks/*` | `git diff` 为空（未改动） |

## 九、异常与失败记录（本轮全部）

| 时间 | 事件 | 判定 |
|---|---|---|
| 01:12:11 | 烟测跑进 `Pool-experiment`（`PoolSmokeTest` 未改指） | 已修复（D5），重跑成功 |
| 01:14:36 | 重指后烟测 5/5 SUCCESS | 通过 |
| 01:17:44 | 双进程争用：`cmin` 被 `probe` 拒绝（`lock_conflict`，rc=3） | 符合设计 |
| 01:25:01 | `PoolRCv05` 正式触发 → `SKIPPED: hour 1 not in [20,21,22,23]` | 正确 |
| 01:35:01 | `PoolRCm` 正式触发 → `SKIPPED: hour 1 not in [20,21,22,23]` | 正确 |
| 01:50 / 02:05 | 健康检查 / 探针（见 §十一 触发器跟踪） | — |

无伪成功、无窗口外采样、无生产变更。

## 十、未解决问题与风险

| # | 项 | 级别 | 说明 |
|---|---|---|---|
| R1 | 无外部告警渠道 | P1 | 失败只落盘；需人工看 `HEALTH.md` |
| R2 | 笔记本电池策略 | P2 | `DisallowStartIfOnBatteries=True`；未接电源/合盖会丢整档 |
| R3 | CN2 归档 EOL 混合 | P3 | 修复后归档 = CRLF(6) + LF(6)，与远端**内容**一致但**字节**不一致；不重写历史 |
| R4 | 历史窗口只有 3 组 | P3 | 与 5 组新数据不可直接合并统计 |
| R5 | `segment_ms` 完成顺序 | P3 | 不与 span 下标对齐；区间可由 `size`+`chunks` 重建 |
| R6 | 广州探针主机身份 | P2（既有） | 未核实，本轮未动 |
| R7 | 跨机器探针无法加锁 | P3 | 只能时间隔离；已用 11-25 同分钟双探针数据佐证影响不显著 |

## 十一、触发器跟踪（本轮实测）

| 时间（CST） | 任务 | 结果 | 说明 |
|---|---|---|---|
| 00:25:01 | PoolRCv05 | rc=3 | 旧代码，窗口外 |
| 00:35:01 | PoolRCm | rc=3 | 旧代码，窗口外 |
| 00:50:00 | PoolHealthCheck | rc=0 | — |
| 01:05:01 | PoolProbeV05 | rc=3 | 窗口外 |
| 01:14:36 | PoolSmokeTest | rc=0 | 新代码，5/5 SUCCESS |
| 01:25:01 | PoolRCv05 | rc=3 | 新代码，窗口外 |
| 01:35:01 | PoolRCm | rc=3 | 新代码，窗口外 |
| 01:50:00 | PoolHealthCheck | 见下方追加 | — |
| 02:05:01 | PoolProbeV05 | 见下方追加 | 窗口外应为 rc=3 |

## 十二、判定表（§6.1）

| 项目 | 判定 | 依据 |
|---|---|---|
| R3 任务映射 | **PASS** | 现行链源码+运行+输出三方一致；历史脚本本体 UNVERIFIABLE（不影响后续） |
| 调度资源审查 | **PASS** | 实际触发器、资源矩阵、逐时点冲突判定齐全 |
| 互斥机制 | **PASS** | 26 单测 + 双进程实测 + 计划任务链路 |
| CN2 历史积压归档 | **PASS** | 12 条对齐远端 SHA、独立归档、未并入 |
| C_MIN 五组配对 | **NOT TESTED** | 未获授权，未产生任何正式流量 |
| 原始数据完整性 | **NOT TESTED**（首轮）/ **PASS**（CN2 12 条、本轮烟测） | 首轮无数据 |
| 本地与远端 SHA | **NOT TESTED**（首轮）/ **PASS**（烟测 41+ 文件、CN2 36 臂） | 首轮无数据 |
| 窗口分类可信度 | **NOT TESTED** | 分类器已实现并单测，但无真实窗口 |
| 首轮实验协议 | **NOT TESTED** | 待授权 |
| v0.5 RC 重新验收 | **BLOCKED** | 需自然劣化窗数据；本轮未跑 RC |

**NOT TESTED 不等于 PASS**：首轮窗口的全部结论要等 12:15（或用户指定窗口）跑完后再填。

## 十三、建议

1. **建议批准 W1 12:15**：健康窗最适合验证协议与数据质量；若今天不合适，任选晚间一档。
2. 批准后我会：改 `suspended` → 提交 → `Enable-ScheduledTask` → 由 `:15` 触发器执行 →
   运行中记录锁/组号/流量 → 结束后做 §5.3 的 13 项检查 → 回填本报告。
3. 首轮通过后再单独申请剩余四个窗口；A/B/C、RC 与 v0.6 Alpha 不放行。
4. 建议后续独立任务处理：外部告警渠道、CN2 归档 EOL 归一化（需批准重写一次）、
   广州探针身份核实、笔记本电源策略。

## 十四、交付物

```
reports/experiments/PN-EXPERIMENT-01/
├── baseline/
│   ├── phase-0_baseline_audit.md
│   ├── phase-0_task_mapping.md
│   └── task-snapshots/{5 个 XML, SHA256SUMS}
├── cn2-backlog/
│   ├── phase-0_cn2_backlog_audit.md
│   ├── manifest.json
│   └── records/cn2-probe-backlog-20261127.jsonl
├── scheduling/
│   ├── phase-1_scheduling_review.md
│   └── phase-1_isolation_implementation.md
├── readiness/
│   └── phase-2_cmin_readiness.md
└── final_report.md
```

代码与测试：`scripts/experiment/{locks,net_telemetry,window_class}.py`（新增）、
`runner.py`/`health.py`/`install_tasks.ps1`（修改）、`scripts/{cmin_experiment,collect_box_probe}.py`（小改）、
`tests/test_experiment_scheduling.py`（26 例）。
