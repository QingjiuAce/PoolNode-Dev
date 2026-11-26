# Phase 2 — 任务失败检测与告警（PN-RECOVERY-01）

> 执行时间：2026-11-27 00:20–00:27 CST
> 结论：**失败可发现**（本地持久化 + 退出码 + 健康面板）；外部通知渠道仍未接入

## 1. 启动前检查（预检，任一失败即 FAILED，不执行正式实验）

| # | 检查 | 失败代码 | 实测（故障注入） |
|---|---|---|---|
| 1 | 脚本存在 | `script_missing:<path>` | ✅ `FAILED: script_missing:...\does-not-exist.py` |
| 2 | Python ≥ 3.10 | `python_too_old` | 未单独注入（版本已知满足） |
| 3 | PyYAML 可导入 | `dependency_missing:PyYAML` | ✅ `FAILED: dependency_missing:...forced-by-injection` |
| 4 | 白名单/配置存在 | `config_missing:<rel>` | 逻辑覆盖，未单独注入 |
| 5 | 输出目录可写 | `output_not_writable` | ✅ `FAILED: out_dir_not_writable:[WinError 5]` |
| 6 | origin 可达（TLS 握手） | `origin_unreachable` | ✅ `FAILED: origin_unreachable:127.0.0.1:1 ...` |
| 7 | sync 的 `~/.ssh` 存在 | `ssh_missing` | 逻辑覆盖 |

## 2. 执行后检查

| # | 检查 | 判据 |
|---|---|---|
| 1 | 退出码 | 非零 → FAILED |
| 2 | 预期文件存在 | 缺失 → `missing_output` |
| 3 | 新增记录数 | 窗口内为 0 → FAILED（sync 例外：允许 0，因远端可能确无新数据） |
| 4 | JSONL 可解析 | 坏行 → `bad_json` |
| 5 | 必要字段 | 每任务定义 `keys`（cmin: ts/cmin/workers/rep；rc: ts/arm/round；probe/sync: ts） |
| 6 | 重复 ID | 同批记录 `keys` 组合重复 → `duplicate_keys` |
| 7 | SHA 字段 | `sha_ok=false` 出现 → `sha_failures`，判失败 |
| 8 | 回传结果 | 上传失败 → PARTIAL（不是 SUCCESS） |

实测：
- `bad_jsonl` → `FAILED: validation: bad_json=1`
- `sha_mismatch` → `FAILED: validation: sha_fail=1`
- `remote_down` → `PARTIAL: upload:connect_failed:NoValidConnectionsError`

## 3. 心跳与告警规则

`health.py` 读取 `state/<task>/*.json`，逐任务计算：

| 条件 | 级别 | 实测 |
|---|---|---|
| 脚本缺失 | CRITICAL | 逻辑覆盖 |
| 连续 ≥2 次失败 | CRITICAL | 逻辑覆盖（`repeated_failure`） |
| 单次失败 | WARNING | 逻辑覆盖 |
| 最近成功早于最近预期窗口 >2h | CRITICAL（`missed_windows`） | 逻辑覆盖 |
| 最近成功早于最近预期窗口（≤2h） | WARNING（`window_pending`） | 逻辑覆盖 |
| 有运行但从未成功 | CRITICAL（`never_succeeded`） | 逻辑覆盖 |
| 无任何运行 | WARNING（`no_runs_yet`） | ✅ 首次运行前实测为 WARNING |

输出：
- `reports/experiment-logs/alerts.current.json`（当前状态，覆盖写）
- `reports/experiment-logs/alerts.jsonl`（**仅状态变化时**追加，避免每小时刷屏）
- `reports/experiment-logs/HEALTH.md`（人类可读表格）
- 退出码：0 OK / 1 WARNING / 2 CRITICAL（可直接被监控系统调用）

`PoolHealthCheck` 计划任务每小时 :50 运行（新增，`StartWhenAvailable`，10 分钟上限）。

## 4. 与 Node Monitor 的边界

按任务书要求，**未**把任务状态写入公开页面：

- 公开 `/monitor/` 只展示节点健康与探针体验，不含实验任务状态；
- 实验状态只落 `reports/experiment-logs/`，不上传公开目录；
- 回传目标是 `/srv/pool/labnode/experiment-inbox/`（回环可达、无 nginx 路由），
  不在 `/srv/pool/data` 之下。

因此 Node Monitor 故障不影响本地任务日志；本地采集也不依赖监控面板。

## 5. 已知缺口

| 缺口 | 影响 | 建议 |
|---|---|---|
| **无外部通知渠道**（邮件/IM/webhook） | CRITICAL 只落盘，仍需人来看 | 后续接一条 webhook 或邮件；`alerts.jsonl` 已是结构化数据，接入成本低 |
| 告警只在 :50 定时检查 | 最迟 1 小时内发现 | 可改为任务结束后立即调用 health |
| `0xFFFD0000` 语义未解 | 只是历史现象，新入口不再产生该码 | 若再出现，先查脚本是否存在 |

**这一缺口的性质**：从"任务失败 17 小时无人知晓"改善为"失败会在 1 小时内出现在
`HEALTH.md` / `alerts.jsonl` 并有非零退出码"，但**仍需人主动查看**。这是本轮能做到的
最小改动，未擅自引入第三方通知依赖。
