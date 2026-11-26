# PN-EXPERIMENT-01-RUN-02 — 阶段 A 最终预检

> 时间：2026-11-27 02:35–02:45 CST（`Asia/Shanghai`，UTC+08:00）
> 分支：`fix/experiment-scheduling-guard` @ `ff2de85`
> 结论：**9 项全部 PASS** → 可提交正式运行授权申请（阶段 B）

## A1. Git 与工作区

| 检查 | 结果 |
|---|---|
| 当前分支 / HEAD | `fix/experiment-scheduling-guard` / **`ff2de85`** |
| 工作区 | 干净（`## fix/experiment-scheduling-guard`，无改动） |
| 恢复基线 | `fix/experiment-pipeline-recovery` = `0d1a522`（未变） |
| Tag | `v0.5.0-rc.1` → `0376444`（未移动） |
| 未审查改动 | 无 |
| push / merge / rebase / cherry-pick | 均未执行 |

本轮新增提交：`1e22264`（一次性授权）、`3f1a325`（key 认证上传）、`ff2de85`（预算硬拦截 + 种子）。

## A2. 正式模式缺陷回归

**输出路径**（正式模式、非烟测）：

```
cmin      --out …\reports\benchmarks\cmin-experiment\runs.jsonl     = 校验路径
rcv05     --out …\reports\benchmarks\v05-rc1        (dir)          = 校验目录
rcmatrix  --out …\reports\benchmarks\v05-chunk-queue (dir)         = 校验目录（不在 Pool-investigate）
probe     --out …\reports\benchmarks\v06-monitoring\local-probe.jsonl
sync      --out …\reports\benchmarks\v06-monitoring\cn2box-probe.jsonl
```

- 端到端用例（`smoke=False`、假脚本、断言 `out_arg == output` 且状态落盘）：
  `test_execute_records_locks_and_uses_the_validated_path`
- 五任务参数化：`test_out_argument_matches_validation_target`
- `test_rcmatrix_never_writes_into_other_worktree`

**CN2 CRLF/LF**：`test_merge_append_accepts_crlf_archive` 与
`test_merge_append_still_rejects_real_divergence`；另在真实归档的临时副本上演练：
追加 6 行、`prefix bytes unchanged=True`、真实文件 SHA 不变。

**回归结果**：`309 passed / 1 skipped`（相对 302 新增 7 个，全部为新增用例）。

## A3. 实验矩阵（脚本枚举，未改动）

| C_MIN | 组 | requested_workers | effective_chunks | 块大小 |
|---|---:|---:|---:|---:|
| 98 304 | single | 1 | 1 | 1 778 514 |
| 98 304 | w8 | 8 | 8 | 222 314 |
| 98 304 | w13 | 13 | 13 | 136 808 |
| 98 304 | w16 | 16 | **16** | 111 157 |
| 131 072 | single | 1 | 1 | 1 778 514 |
| 131 072 | w8 | 8 | 8 | 222 314 |
| 131 072 | w13 | 13 | 13 | 136 808 |
| 131 072 | w16 | 16 | **13** | 136 808 |

- 128K 下 **w13 与 w16 的 effective_chunks 都是 13**（`floor(1 778 514 / 131 072) = 13`），
  这是 C_MIN 约束的自然结果；报告中不会把 `requested_workers=16` 写成 16 块。
- 5 reps × 8 次下载 = **40 次下载 / 20 对**。
- 种子 `20261127` 复算：40 条、20 对、**配对违规 0**；每组内两个 C_MIN 背靠背，组间顺序随机、
  组内顺序随机（实算第 1 个 rep 顺序：w16(96K) → w16(128K) → w8(128K) → w8(96K) → …）。

## A4. 120 MB 自动硬拦截（本轮新增，已在测量路径内生效）

实现：`scripts/budget_guard.py` + `peak_probe._fetch`。

| 要求 | 实现 |
|---|---|
| 请求启动前预留 | `_fetch` 在 `HTTPSConnection(...)` **之前** `reserve(expected)` |
| 预留计入已用 | `reserve` 同时累加 reserved；判断用 `spent + reserved + n` |
| 超过上限拒绝该请求 | `projected > limit` → 置 `stopped`，抛 `BudgetExceeded`，不建连接 |
| 并发原子 | 全部在同一把线程锁内完成（`test_budget_is_thread_safe`：8 线程 × 50 次，恰好 10 次通过） |
| 预留释放 | **从不释放**：无法证明"字节没发出"时按已用计（保守） |
| 重试/失败响应体计入 | 每次 `_fetch`（含重试）各自预留；成功与否都 `commit` |
| 达到上限后停止 | `stopped=True` 后所有 `reserve` 立即拒绝（连 0 字节也拒） |
| 不依赖人工发现 | 由子进程内的计数器拦截，runner 只读取结果 |

**实测（低配限流演示，故意把上限设为 1 000 000 B）**：

```
POOL_BUDGET_LIMIT=1000000 python scripts/cmin_experiment.py --reps 1 --cmin 98304 --seed 20261127
→ 9 次请求放行、消耗 889 258 B，第 10 次预留被拒，随后 8 次请求全部拒绝（0 字节外发）
→ budget.json: {"limit":1000000,"spent":889258,"requests":9,"rejected":8,"stopped":true}
→ 4 条记录均为 status=error / error="BudgetExceeded: …"，脚本退出码 1
```

即：**上限被突破了 0 字节**；剩余臂仍逐条留痕，但不再发包。
`runner` 检测到 `budget.stopped=true` 时记 `PARTIAL`（不是 FAILED），已完成数据照常校验与归档。

**正式窗口**：上限 120 000 000 B，计划 71 140 560 B（59%），
即使全部 30 个分段臂各整臂重试一次（+53.3 MB）也会在越过 120 MB 前被拦下。

**发现并修复的一个真实缺陷**：`Budget.dump()` 在持锁路径上再次取同一把非重入锁，
带状态文件的第一次 `commit` 会**死锁**。已改 `RLock`，并新增
`test_budget_dump_does_not_deadlock`（5 秒超时断言），避免以"挂住套件"的形式复发。

## A5. 一次性调度门控

| 检查 | 结果 |
|---|---|
| `PoolCminExp` | **Disabled**，`StartWhenAvailable=False`（错过不补跑） |
| `suspended` | `True`（`runner.TASKS["cmin"]`） |
| 授权文件缺失 | `SKIPPED: no_authorization:<YYYY-MM-DDTHH>` |
| 小时/日期不匹配 | `SKIPPED: authorization_invalid:…` |
| 已消费 | `SKIPPED: authorization_already_consumed:<rid>`（`O_EXCL` 原子标记） |
| 预算超限 | `FAILED: budget_exceeds_authorization` 且**不消费**授权 |
| 其他窗口 | 20–23 点无授权文件 → 永不执行 |
| 其他任务 | `PoolRCv05/RCm/ProbeV05/ProbeSync` 均无 `requires_authorization` 放行逻辑；RC/探针窗口与本次无关 |

授权文件（待你授权后创建）：

```json
{"task":"cmin","date":"2026-11-27","hour":12,"minute":15,"timezone":"Asia/Shanghai",
 "max_bytes":120000000,"single_use":true,"seed":20261127,
 "approved_by":"<你的确认>","approved_at":"<ISO>"}
```

## A6. SSH 管理边界

本阶段**未执行任何 SSH 检查**：没有连接探测、没有指纹读取/比较、没有修改
`known_hosts`/SSH 配置、没有 TOFU。归档通道沿用既有配置，失败即
`REMOTE_ARCHIVE_PENDING`，不自行修复。

## A7. 最终预检结论

| 项目 | 结果 |
|---|---|
| Git / 工作区 | **PASS** |
| 正式模式输出路径 | **PASS** |
| CRLF/LF 归档兼容 | **PASS** |
| 实验矩阵 | **PASS** |
| 配对随机化（seed 20261127，违规 0） | **PASS** |
| 120 MB 自动硬拦截 | **PASS** |
| 一次性调度门控 | **PASS** |
| 其他实验隔离 | **PASS** |
| 生产只读边界（本阶段无生产写入） | **PASS** |

**全部 PASS → 进入阶段 B，等待明确授权。**
任务仍 `Disabled`、`suspended=True`、无授权文件、流量 0。
