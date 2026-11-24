# v0.5 基线审查报告（D1）

> 审查对象：`feature/peak-probe-tooling` @ `1a9c825`（含 v0.5 提交 `376a028`/`68e66a0`）
> 审查日期：2026-11-25 · 对应任务书 TASK-1.1 / TASK-1.2
> 结论：**未发现高危正确性缺陷；有 4 项设计限制需在 v0.6 处理；合并前还需 5 项补充测试**

## 1. 审查范围

`pool/range_transport.py` 中的 `_ranges`（静态）、`_chunk_spans`、`_dynamic`（动态队列）、`download`（调度选择/回退/发布），以及 `pool/fetch.py` 的参数面（`--scheduler`、`--metrics`、`--workers 1..16`）。

## 2. 任务书 8 项检查点逐条结论

| # | 检查点 | 结论 | 依据 |
|---|---|---|---|
| 1 | worker 退出后任务遗失 | ✅ 无泄漏 | 失败时 `cancel.set()` → `pool.map` 抛异常 → `with ThreadPoolExecutor` 退出时 join 全部 worker；剩余 QUEUED 块被丢弃且 stage 由单流回退 `'wb'` 截断 |
| 2 | 取消后连接仍占用 | ✅ 已释放 | worker `finally` 中 `sink.close()` + `conn.close()`；在途响应由 `_fetch` 的 `with response` 关闭；`pool` 上下文保证 join |
| 3 | 重试导致重复写入 | ✅ 安全 | 每块重试从块起点 `seek(start)` 整块重写，写区间固定在块内；块间不重叠 → 幂等 |
| 4 | 文件长度不一致 | ✅ 由校验拦截 | 块覆盖整个文件（整数除法边界并集 = [0, size)），`_verify` 先查 `fstat().st_size == size` 再 SHA |
| 5 | 服务器不支持 Range | ✅ 正确回退 | 非 206 → `_InvalidResponse` → `download` 捕获 → 单流 GET 重下 + 校验；测试 `server_ignores_range_falls_back` |
| 6 | 200 / 206 / 416 处理 | ✅ 明确 | span 必须 206、整文件必须 200（否则 `_InvalidResponse`）；416 落入 `_InvalidResponse` → 回退单流（尺寸错误时单流也会失败 → 终止并报错） |
| 7 | 异常时保留临时文件 | ✅ 已清理 | `download` 的 `finally: stage.unlink()`；单流回退前 `'wb'` 截断 |
| 8 | 最终完整性校验 | ✅ 严格 | 仅 `_verify` 通过后 `os.link(stage, output)`；绝不覆盖同名文件 |

## 3. 附加审查发现（非缺陷，属 v0.6 待办）

| # | 发现 | 级别 | 说明 |
|---|---|---|---|
| F1 | **chunk 数与 worker 数耦合** | 设计限制 | `_CHUNK_TARGET=256KiB` + `n ≤ 2×workers`：5.4MB 只有 21 块，workers≥11 即封顶，无法实现 32 流 |
| F2 | **无慢段对冲** | 设计限制 | 事件内 w32 出现尾比 4.41（24日00:05），单慢段可拖死整体；任务书 TASK-2.4 目标 |
| F3 | **探测请求多付一次握手** | 优化点 | probe 走独立新连接（17 次握手），可改为 worker0 的探测+复用，省 1 次 TLS（约 120ms） |
| F4 | **chunk 指标不含字节数** | 可观测性 | `kind:'chunks'` 记录有状态/耗时，无 per-chunk 实际写入字节；v0.6 对冲审计需要 |
| F5 | **keep-alive 关闭检测依赖失败重试** | 已兜底 | `conn.sock is None` 检测可能漏判服务端关闭，但下一次请求失败会被 `_NetworkError` 重试覆盖（fresh conn）；测试 `server_closes_keepalive_reopens` 已覆盖退化路径 |
| F6 | **单块失败即整臂回退** | 策略问题 | 事件内单块两次失败 → 放弃全部已完成块转单流；v0.6 可考虑"仅重排失败块"（有界） |

## 4. 补充测试 T1–T5（2026-11-25 完成）

| # | 场景 | 测试 | 结果 | 关键断言 |
|---|---|---|---|---|
| T1 | worker 打不开 staging 句柄（本地 I/O）| `test_t1_local_sink_open_failure_is_terminal` | ✅ | 抛 OSError、不回退、无输出、无 `.part` 残留 |
| T2 | 磁盘写满（短写）| `test_t2_short_local_write_is_terminal` | ✅ | `OSError('short local write')` |
| T3 | 外部取消（cancel Event 置位）| `test_t3_external_cancel_aborts_without_publish` | ✅ | 抛 `Cancelled`、**不发布**、无 IN_PROGRESS 残留；在途块可完成、剩余保持 QUEUED |
| T4 | 重试耗尽 | `test_t4_retry_exhausted_chunk_fails_then_single_fallback` | ✅ | 恰好 1 个 FAILED、其他块 SUCCEEDED、**并行组装被丢弃**、仅单流回退发布（SHA 有效）|
| T5 | 连接建立异常 | `test_t5_connection_setup_failure_falls_back` | ✅ | 视为传输错误 → 重试 1 次 → 单流回退；无临时残留 |

**测试基线：107 passed / 2 skipped**（原 102 + T1–T5）。

### 为实现 T1–T5 所做的三处最小修复

| 修复 | 文件 | 说明 |
|---|---|---|
| `Cancelled(RuntimeError)` + `cancel` 参数 | `pool/range_transport.py` | 外部取消（可选 `threading.Event`）在 dynamic 路径生效：worker 在领取前检查、已领取块标记 CANCELLED、抛 `Cancelled` 且**不回退**；static 路径不观察该参数。CLI 未传则行为不变。 |
| 连接建立错误归类 | 同上 | `_open_conn` 抛出的 OSError/HTTPException 包装为 `_NetworkError` → 享受"重试 1 次 + 单流回退"，避免整次下载因构造连接失败而终止 |
| 重试计数修正 | 同上 | `retries` 先前把最后那次失败也计入（每块多计 1）；改为只统计**已安排的重试**（每块最多 1） |

> T3 语义说明：取消是 best-effort —— 已在途的块会跑完（≤1 块/worker），队列中未领取的块保持 QUEUED，最终不发布任何内容。T4 的"不发布"指并行组装结果被丢弃；回退单流重新下载并校验后发布，属设计内安全路径。

## 5. 结论

- **v0.5 代码质量可作为 v0.6 开发基线**（无高危缺陷，回退路径完整）
- **T1–T5 已全部通过，合并门槛的测试项满足**；剩余门槛为性能回归与配置审查
- **不建议直接分发**：F1/F2（chunk/worker 耦合、无对冲）为已知能力上限，由 v0.6 处理
- 合并路径：v0.6 在 v0.5 + 本节修复基础上开发；合并前补性能回归
