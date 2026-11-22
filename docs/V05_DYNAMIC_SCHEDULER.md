# v0.5 — Range 下载动态调度与尾延迟优化（设计与实测）

> 任务书：PoolNode v0.5（2026-11-23 深夜）· 基线：v0.4-beta（`4e3dcd5`）
> 状态：**已实现 + 同窗口配对实测**；深劣化事件窗（事件级）配对留待下一事件验证
> 原始数据：`reports/benchmarks/v05-pairs/{runs.jsonl, runs_cap4_round1.jsonl, metrics/}`

## 1. 根因判断（按证据等级）

**已确认（源码审查 + 指标实测）**
- v0.4 每请求新建连接且显式 `Connection: close`：w16 = 1 探测 + 16 分片 = **17 次 TCP+TLS 握手**，零复用（服务器日志逐轮精确 17 连接佐证）。
- 固定等分 + 一次性绑定：完成时间 = 最慢分片。实测段速离散 134–847 KB/s（6 倍），Body 耗时 101–728 ms（7 倍）。
- 探测请求串行阻塞关键路径（16 分片须等探测完成，多付 1 次建连+TLS+TTFB）。
- 指标口径：同窗口交替配对下 **B(client w16) 14.31 ≈ C(curl Range) 14.56 Mbps（差 ~2%）**——
  原始记录中"B 比 A1 低 14–23%"的差距在严格配对下基本消失，属窗口漂移混杂，非客户端开销。

**高度可能（机制数据支持，端到端未在事件窗复验）**
- 尾等待是固定等分的主要损失来源；动态队列把它转化为"快 worker 领下一块"。
- 连接复用收益大：复用连接上 chunk TTFB 中位 **55 ms vs 新建 285 ms**（省握手 ~120 ms
  + TCP 慢启动 + 服务器热连接路径），共省 ~230 ms/请求。

**尚未证实**
- 事件级深劣化窗口（单流 0.4–0.6 Mbps、22:00 型）下的 B/D 端到端配对——事件 3 于
  01:0x 结束，未能捕获。机制数据（段速高方差）预测 D 在该窗口优势更大，待验证。
- 22:00 的 1.40 Mbps 低谷**不能归因于客户端**：客户端开销修正后（B≈C），该低谷与
  时间曲线中同窗口的单流/16 流劣化一致，属链路侧时段性劣化。

**已排除（本轮验证范围内）**
- "客户端固定开销造成 B 与 A1 的 14–23% 差距"——同窗口配对下差距 ~2%。

## 2. v0.5 设计

### 2.1 任务模型（§4.1）
- `_chunk_spans(size, workers)`：块数 = `clamp(ceil(size/256KiB), 1, 2×workers)`；
  边界复用 v0.4 的整数除法公式，**严格无缝、无重叠**（性质测试锁定）。
  小文件块数自然减少（1.7MB→7 块、2MB→8 块），effective workers = min(workers, 块数)。
- 每块状态机 QUEUED→IN_PROGRESS→SUCCEEDED/FAILED/CANCELLED，含 worker、retries、
  claim/done 时间（metrics 启用时导出 `kind:'chunks'` 记录）。

### 2.2 Worker 模型（§4.2）
- `queue.Queue` 原子领取（`get_nowait`），双 worker 不可能领到同一块（测试锁定）。
- 每 worker 拥有一条 keep-alive 连接，顺序服务多块；响应指示关闭时安全重建。
- 失败语义与 v0.4 完全一致：每块 `_NetworkError` 重试 1 次（换新连接、整块重传），
  仍失败或协议校验失败 → 取消事件停止领新块 → 整体回退单流 GET（truncate stage）。

### 2.3 参数（§4.3，由实测决定）
- **chunk target 256KiB**：沿用任务书口径；更小段受握手/慢启动反噬（A1 106KB 段
  4.77–5.12 vs A2 长流 5.53–6.33 Mbps 的既有证据）。
- **每 worker 块上限 4 → 2**（第一轮实测后修正）：w4 时 21 块/4 worker = 最长 6 班
  串行链，中位落后 static 60%；cap=2 后收敛到 −15% 且 w16 从持平转为 +52%。
- **默认调度 `auto`**：≥4MiB 用 dynamic（实测胜出区），小文件保持 static（实测
  static 胜出区：smoke −22% / bench-2m −22% 归因于 worker 自动缩减减少了并发流数，
  快窗口下 16 短段并行更强）。显式 `--scheduler dynamic/static` 可强制。

### 2.4 连接复用（§4.4）
- 复用仅在 caller-owned 连接上发生（`conn` 参数）；服务端关闭（`Connection: close`
  响应或 nginx keepalive 超时）→ `conn.sock is None` → 下一块重建。握手不计入
  正确性路径，失败重试换新连接，不影响安全语义。

### 2.5 慢任务与取消（§4.5）
- 不因慢取消：慢 worker 只是被快 worker 抢走后续块。
- 取消 = 失败后 `cancel` 事件 + 队列停止领取；15s socket 超时 + 每块 ≤1 重试 +
  下载级单流回退 ≤1 次，全部有界；无投机/对冲请求（第二阶段可选）。

## 3. 完整性与安全回归（§5，最高优先级）

静态路径**零改动**（默认回退保留）；动态路径复用同一 `_fetch` 校验原语
（206/Content-Range 精确匹配/强 ETag/If-Range/CL/identity/越界拒绝）。离线回归
**95 passed / 2 skipped**（12 项新增），故障注入覆盖：

| 注入场景 | 测试 | 结果 |
| --- | --- | --- |
| 单 chunk 超时 | `one_network_failure_retries_chunk` | 整块重试成功（retries=1），SHA ✓ |
| Worker 中途断开 | `persistent_failure_falls_back_and_cancels` | 回退单流；无 IN_PROGRESS 残留 |
| Chunk 错误长度（短体） | `chunk_short_body_falls_back` | 回退单流，内容正确 |
| ETag 中途改变 | `etag_change_falls_back` | 回退单流，内容正确 |
| 服务端忽略 Range（200） | `server_ignores_range_falls_back` | 回退单流，内容正确 |
| 双 worker 竞争领取 / 重复提交 | `publish_reuses_connections` | 每块恰好请求一次（集合相等） |
| 取消期间收到数据 | `persistent_failure...` | stage 被 'wb' 截断，发布内容正确 |
| 服务端关闭 keep-alive | `server_closes_keepalive_reopens` | 每请求安全重建，SHA ✓ |
| Chunk 内容损坏 | `corrupt_chunk_recovers_via_single` | SHA 拦截 → 单流恢复干净数据 |
| 发布失败（os.link） | `publish_failure_is_terminal` | 本地 I/O 终止，不回退不发布 |
| SHA 不匹配 | （static `bad_all` 既有 + 上述损坏场景） | 不发布损坏文件 |
| 临时文件写入失败 | 结构同 static 路径（OSError 向上传播不发布） | 未直接注入¹ |

¹ 真实文件对象不会短写，无法离线确定性注入；以 `os.link` 失败覆盖本地 I/O 终止
路径，且 `_fetch` 的 `short local write` 分支与 static 完全同一代码。

## 4. 性能对照（§6）

**窗口声明**：2026-11-23 01:08–01:33，事件 3（21:00–01:0x）刚结束的恢复震荡窗
（单流 5.6–49 Mbps 秒级波动）。全部对比为**同批次交替配对**（round 内 B/D 交替，
间隔 ~3s）。n=5–10；中位数；Mbps = 5,403,720B×8÷墙钟（单文件聚合口径）。

### bench-6m（5.4MB 基准图）

| 工况 | B static（v0.4）| D dynamic（v0.5）| 配对差（逐对中位）|
| --- | --- | --- | --- |
| w16，n=10 | 中位 27.45（13.29–38.11）| **中位 33.62（23.48–41.54）**| **D +6.2，胜 7/10 对** |
| w8，n=5 | 中位 25.09（17.18–42.27）| **中位 29.72（25.43–40.95）**| **D +4.6，胜 4/5 对** |
| w4，n=5 | 中位 39.81（19.66–40.53）| 34.53（15.62–38.83）| B +4.4，胜 4/5 对 |
| A 单流，n=5 | 5.59–49.09（窗口噪声大）| — | — |
| C curl-Range ×16，n=5 | 中位 28.49 | — | B≈C（§1 已确认项）|

关键观察：**D 的最差 case（23.48）比 B 的最差 case（13.29）高 77%**——B 掉进
13–17 Mbps 的瞬间（微劣化），同 3s 内 D 拿到 25–41 Mbps。这是尾等待吸收机制的
直接端到端证据：快 worker 领走慢流欠下的字节。

### 文件大小覆盖（w16，n=5）

| 文件 | B static | D dynamic | 判定 |
| --- | --- | --- | --- |
| 5.4MB | 27.45* | 33.62* | D 胜（→ auto 采用 dynamic）|
| 2.0MB | 15.36 | 12.03 | static 胜（worker 缩减减少并发流）|
| 1.7MB | 16.07 | 12.54 | static 胜（同上；绝对差 ~250ms）|

*bench-6m 为 n=10 合并口径；其余 n=5。

### 连接与握手

| 指标 | B static | D dynamic |
| --- | --- | --- |
| 握手数（w16, 5.4MB）| 17（1 探测+16 段）| 17（1 探测+16 worker 连接，21 块复用）|
| 复用连接上 TTFB | —（无复用）| **55 ms vs 新建 285 ms** |

## 5. 已知限制

1. **事件级深劣化窗未配对**：机制与微劣化瞬间证据支持 D 在深劣化下优势更大，
   但端到端配对需等待下一事件（时间曲线 cron 采样至 23:00，事件窗出现时补测）。
2. w4（或显式低 worker 数）下 2 班串行链仍有 ~15% 代价；auto 策略靠默认 tier
   （≥2MB→w16）规避，强制 `--workers 4` 的用户属实验行为。
3. 小文件 worker 缩减在快窗口损失并发流数（−22%）；深劣化下预期反转（更大块
   摊薄慢启动），未验证。
4. 单客户端（中国移动）× 单夜窗口；多运营商/多夜采样未完成。
5. 复用连接若被中间盒静默杀死，首请求需等 15s socket 超时才触发重试（有界，
   未观察到；记为已知风险）。

## 6. 结论（§G）

- **值得采用（有条件）**：`auto` 默认策略下，≥4MiB 文件走 dynamic（w16 中位 +22%，
  最差 case +77%，零完整性失败 10/10 SHA），小文件保持 static（无退化）。
- 动态调度收益**可重复**：同窗交替配对下 w16 胜 7/10 对、w8 胜 4/5 对；
  cap 4→2 的参数修正使 w4 退化从 −60% 收敛到 −15%。
- **正常网络退化**：大文件无退化（反而更稳）；小文件有（已由 auto 规避）。
- **v0.4 回退路径保留**：`--scheduler static` 强制 v0.4 行为，代码零改动。
- 具备进入下一阶段条件：事件窗补测 + 多夜采样后，可考虑把 auto 阈值/worker
  缩减规则按数据再调。

## 7. 复现

```sh
# 回归（离线）
python -m pytest tests/ -q

# 配对基准（同窗口交替；避开 cron 偶数小时 ±5min）
python scripts/bench_v05_pairs.py --pairs 5 --arms B,D --workers 16 \
  --asset bench-6m --assets <受信白名单> --out reports/benchmarks/v05-pairs

# 指标观察（逐请求 DNS/TCP/TLS/TTFB/Body + chunk 状态表）
python -m pool.fetch <asset> out.jpg --assets <白名单> --workers 16 --metrics m.jsonl
```
