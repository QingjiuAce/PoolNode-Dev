# PN-EXPERIMENT-01-RUN — 阶段 A 正式执行前核验

> 核验时间：2026-11-27 02:10–02:30 CST（`Asia/Shanghai`，UTC+08:00）
> 分支：`fix/experiment-scheduling-guard` @ `e8d1988` → 本轮新增 `1e22264`（一次性授权门控）
> 结论：**技术预检通过；但发现一项阻断级生产异常（SSH 主机密钥变更），窗口不得启动**

## A1. Git 与代码基线

| 项 | 值 |
|---|---|
| 调度分支 HEAD | `1e22264`（核验开始时 `e8d1988`） |
| 工作区 | 干净（核验开始时 `## fix/experiment-scheduling-guard`，无改动） |
| 恢复基线 `fix/experiment-pipeline-recovery` | `0d1a522`（未变） |
| 其他 worktree | `Pool`=`4cb8b04`、`Pool-experiment`=`0d1a522`、`Pool-investigate`=`98a4e0a`、`Pool-main`=`4f3dbad`（均未变） |
| 互斥实现 | `scripts/experiment/locks.py`（8 020 B）、`net_telemetry.py`、`window_class.py` 均在位 |
| tag | `v0.5.0-rc.1` → `0376444`（未移动） |
| 未提交代码/配置/数据 | 无 |

未推送、未合并、未切换任何分支。

## A2. 两个正式模式缺陷的回归验证

### A2-1 `--out` 绑定（正式模式，非烟测）

`execute()` 在**两种模式**下走同一段注入代码：

```python
base = list(task["smoke_args"] if smoke else task["args"])
args = strip_out(base) + ["--out", out_target(task, target)]
```

正式模式（`out_dir=None`，`target=ROOT/task["output"]`）实测：

| 任务 | 注入的 `--out` | 校验读取 | 一致 |
|---|---|---|---|
| cmin | `…\reports\benchmarks\cmin-experiment\runs.jsonl` | 同 | ✅ |
| rcv05 | `…\reports\benchmarks\v05-rc1`（目录） | `…\v05-rc1\runs.jsonl` | ✅ |
| rcmatrix | `…\reports\benchmarks\v05-chunk-queue`（目录） | `…\v05-chunk-queue\runs.jsonl` | ✅ |
| probe | `…\reports\benchmarks\v06-monitoring\local-probe.jsonl` | 同 | ✅ |
| sync | `…\reports\benchmarks\v06-monitoring\cn2box-probe.jsonl` | 同 | ✅ |

- `rcmatrix` 的注入路径在本仓库内；若**不**注入，`rc_matrix.py` 的默认输出会是
  `F:\鲸鲸\GitHub\开源二创\Pool-investigate\reports\benchmarks\v05-chunk-queue`——
  即会写入另一个 worktree。本轮已确认注入生效。
- 正式模式（`smoke=False`）的端到端证据：`tests/test_experiment_scheduling.py::
  test_execute_records_locks_and_uses_the_validated_path`（假脚本、正式路径、
  `out_arg == output`、状态落盘），不是烟测结果。
- 参数化用例 `test_out_argument_matches_validation_target` 覆盖全部 5 个任务。

### A2-2 CN2 CRLF/LF

在**真实归档的临时副本**上跑正式合并函数：

```
real archive : 6 lines, sha256 dda264af232ea396…, CRLF=6
remote       : 12 lines, sha256 0cc764ffefa43c3b…
added lines  : 6
prefix bytes unchanged : True          # 历史 6 行逐字节保留
normalized content == remote : True
real file unchanged : True             # 真实归档未被触碰
```

- 比较逻辑：`remote.startswith(local)` 失败时，用 `local.replace(b"\r\n", b"\n")`
  再比；通过则只追加 `remote[len(normalized):]`。
- 不存在重写或截断：追加模式 `open(...,"ab")` + `fsync`，前置校验失败即抛出。
- 历史 6 行未被改写；本地记录数 6 → 12、远端 12，SHA 语义见阶段 0 报告。

## A3. 实验矩阵确认（从脚本源码枚举，不猜测）

`scripts/cmin_experiment.py`：`ARMS = [(1,'single'),(8,'w8'),(13,'w13'),(16,'w16')]`，
`plan(size, workers, cmin) = min(workers, size // cmin)`，fixture `smoke` =
`/submissions/originals/pool-public-smoke-test.jpg`，1 778 514 B。

| C_MIN | 组 | workers | 实际 chunks | 块大小 | 每臂请求数 |
|---|---:|---:|---:|---:|---:|
| 98 304 | single | 1 | 1 | 1 778 514 | 1 |
| 98 304 | w8 | 8 | 8 | 222 314 | 9 |
| 98 304 | w13 | 13 | 13 | 136 808 | 14 |
| 98 304 | w16 | 16 | **16** | 111 157 | 17 |
| 131 072 | single | 1 | 1 | 1 778 514 | 1 |
| 131 072 | w8 | 8 | 8 | 222 314 | 9 |
| 131 072 | w13 | 13 | 13 | 136 808 | 14 |
| 131 072 | w16 | 16 | **13** | 136 808 | 14 |

**必须注意**：128K 时 `size // 131072 = 13`，所以 `w16` 组实际只有 **13 条流**
（这是 C_MIN 约束的直接体现，不是缺陷）。

- 每 rep 8 次下载 = 4 组 × 2 个 C_MIN；5 reps → **40 次下载 = 20 个背靠背配对**。
- 组内：同一 worker 组的两个 C_MIN 背靠背，顺序随机；组间：4 个组每 rep 重排。
- 随机种子：`secrets.randbits(32)`，写入每条记录；`seq` 记录实际执行序号。
- 用种子 `20261127` 复算：40 条、20 对、配对违规 **0**。
- 统计口径：每对 = 同 rep 同 worker 组的 96K vs 128K，配对差按相对值比较；
  不跨 rep 配对、不跨档位合并。

与"五组配对"措辞的关系：任务书的"五组"对应 **5 个 rep**（每个 rep 内含 4 个
worker 组各一对），与 phase-4 预算算式 `2 C_MIN × 4 臂 × 5 组 × 1.7 MB ≈ 68 MB` 一致。
**20 对与已审计协议一致**，按 20 对执行。

## A4. 流量预算

| 项 | 值 |
|---|---|
| 计划下载量 | 40 × 1 778 514 B = **71 140 560 B = 71.1 MB** |
| 探针请求 | 分段臂各 1 次 1 字节 Range：30 次 × ≈1 B，可忽略 |
| 回传 | 日志 + 状态 + 40 行 JSONL ≈ 200 KB |
| 重试上限 | `work(span)` 每段最多 2 次尝试；单流臂不重试 |
| **理论最坏** | 30 个分段臂全部整臂重试一次 ≈ 71.1 + 53.3 = **124.4 MB** |
| 硬上限 | **120 MB** |

**冲突**：理论最坏 124.4 MB 超过 120 MB 上限 4.4 MB。实际触发条件是链路全程失败/
重试风暴，那时应当人工中止。请确认口径（见最终申请 §3）。

服务器侧"本月流量余量"**无法读取**：读取需要 SSH，而 SSH 主机身份异常（见 §A6）。
已知约束（11-26 审计）：1 TB/月流量池，当月此前用量远低于上限；本次 71 MB
占池 0.007%，即使按最坏 124 MB 也仅 0.012%。

自动重试不会无限增长：单流臂无重试，分段臂每段 2 次上限，runner 不补跑。

## A5. 调度门控

| 检查 | 结果 |
|---|---|
| `PoolCminExp` 当前状态 | **Disabled**（01:05:37 起） |
| `suspended` 双保险 | `runner.TASKS["cmin"]["suspended"] = True`（HEAD `1e22264`） |
| 一次性授权机制 | **本轮新增并通过 4 个测试**（详见下） |
| `StartWhenAvailable` | **False** → 错过触发不补跑 |
| `MultipleInstances` | `IgnoreNew` |
| 20–23 点窗口 | 无对应小时的授权文件 → `SKIPPED: no_authorization`；且任务禁用 |
| 同链路其他任务 | `:05` probe（16 MB，通常 20–50 s）；`:25` rcv05 与 `:35` rcmatrix 在 12 点不在窗口 → SKIPPED |

### 一次性授权机制（§A5 要求的"可靠一次性运行门控"）

`runner` 对 `requires_authorization` 的任务（当前仅 cmin）在**任何网络动作之前**：

1. 读取 `reports/experiment-logs/authorizations/cmin-YYYY-MM-DDTHH.json`；
   文件缺失 → `SKIPPED: no_authorization`。
2. 校验 `task / date / hour / minute(±10) / single_use=true`；不符 →
   `SKIPPED: authorization_invalid:<code>`。
3. 校验流量计划：`plan.downloads × plan.bytes_each > max_bytes` →
   `FAILED: budget_exceeds_authorization`，**不消费授权**。
4. `O_CREAT|O_EXCL` 创建 `.consumed.json` 并写入 run_id；已存在 →
   `SKIPPED: authorization_already_consumed:<rid>`。

因此：即使任务被重新启用并保持每小时触发，**同一窗口只能跑一次**，
12 点以外的任何小时都没有可用授权。测试覆盖：缺授权、重复使用、跨窗口、
预算超限（不消费）。

## A6. 阻断项：SSH 主机密钥变更（生产侧异常）

| 项 | 01:53 时 | 02:16 时 |
|---|---|---|
| HK `156.233.227.20` ed25519 | `SHA256:1dUGXBUOxkQU6xHmp/ERW+pQRYLLG/ODpjwbbJQWucY`（固定文件） | `SHA256:MlHlKSmWIqL4swLGNKDUd/sec6X93Zf+9hxTAo7WptU` |
| HK ecdsa | `SHA256:MJOXmSJWPpnxNjPprd0b3wyz0XlkDkhqOeSckMdxlsg` | `SHA256:Yk3l/F7LTiTgc7rnDgN09eFNg+Zt/aWP7a2kC9p39I0` |
| HK rsa | `SHA256:TbfefLcsi5zY4nhdynss5CburgqPHIGvc3QRA1liqEY` | `SHA256:/UsDyMSSRkVPh6YZ2Z1pgcyOXZSvRWJmIYIZno3izJU` |
| CN2 `160.202.47.187` ed25519 | 连接成功 | `SHA256:F2PhHqAjuthaIVZsdiQJbjtv866Tsu2mLgvavfr2rdg`（与 `known_hosts` 记录不符） |

### 已排除"换了台机器/MITM"的证据

`https://dl.bszx.site/monitor/status.json`（生产机自带监控，02:22 生成）：

```json
{"hostname":"ser103837614816", "uptime_s":1072, "os":"Debian GNU/Linux 12 (bookworm)",
 "kernel":"6.1.0-10-amd64", "srv_pool_mounted":true, "services":{"nginx":"active"},
 "integrity":{"state":"ok","files":44,"bytes":117549350,"verified":44,"corrupted":0,"missing":0},
 "cert":{"not_after":"2027-02-18T22:05:12+00:00"}}
```

- 主机名 `ser103837614816` 与 `_pool-cloud-lab/logs/01_recon.txt` 记录一致 → **同一台机器**；
- 44 个文件 / **117 549 350 字节**与 01:51 的只读核对完全一致 → **数据未变**；
- 证书到期日未变 → **同一证书**；
- `uptime_s = 1072`（≈17.9 分钟）→ **机器在 02:01 左右重启过**。

**最可能的解释**：02:01 的重启触发了 SSH 主机密钥重建（cloud-init 的
`ssh_deletekeys` 或管理员动作），因此 01:53 与 02:16 会看到不同密钥。
这比"路径拦截"更符合全部观测（MITM 不会让机器 uptime 归零、也不会保持 44/117549350 不变）。

### 仍然阻断的原因

- 即使解释合理，**新密钥尚未经带外通道确认**；上传器使用 `RejectPolicy`，现在上传必然失败；
- 02:01 的**重启本身就是未预告的生产变更**（nginx 已自愈且数据完好，但按 §9 必须上报）；
- 我**没有**向任何未验证主机发送密码：paramiko 在校验失败时不会进入认证阶段。

### 两个盒子都受影响

CN2 `160.202.47.187` 同样三个密钥全部变更：

| 类型 | 本机 `known_hosts` 记录 | 当前实际 |
|---|---|---|
| ed25519 | `SHA256:ouXt4b//…r+E` | `SHA256:F2PhHqAj…rdg` |
| ecdsa | `SHA256:5odfCp7a…VJc` | `SHA256:1ltGYz4S…3Lo` |
| rsa | `SHA256:k4zEE/C/…BlCE` | `SHA256:KMRixmml…XPY` |

后果（除上传外）：

- `PoolProbeSync`（每日 23:40）用 `ssh/scp` + `BatchMode` + 默认 `known_hosts`，
  今晚会因主机密钥不符而失败（fail-closed，不会写坏归档；CN2 侧数据仍只增，
  修复后一次补齐）。
- 本机 `~/.ssh/known_hosts` 在 02:16–02:17 被某个 OpenSSH 客户端动作改写
  （出现 `known_hosts.old`，新增了 HK 的 ecdsa/rsa 条目）；该文件不参与上传器校验。

### 解除条件（任一）

1. 你在云控制台/VNC 里执行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`，把指纹发我核对
   → 我更新 `~/.ssh/pool-lab/known_hosts_hk_lab` 并复验（读取 `/srv/pool/data` 计数与收件箱）；
2. 或你直接授权我按"重启后重生成"的推断重新固定（TOFU），随即用只读命令验证数据一致性。

## A7. 阶段 A 判定

| 检查 | 结果 |
|---|---|
| A1 Git/工作区 | PASS |
| A2-1 正式模式 `--out` | PASS（正式模式端到端用例 + 全任务参数化） |
| A2-2 CN2 CRLF/LF | PASS（临时副本演练，历史字节不变） |
| A3 实验矩阵 | PASS（20 对，与协议一致；128K 的 w16 实为 13 块已注明） |
| A4 流量预算 | **CONDITIONAL**（最坏 124.4 MB > 120 MB；需确认口径；月度余量不可读） |
| A5 调度门控 | PASS（一次性授权 + 双保险 + 无补跑） |
| A6 生产身份 | **FAIL（阻断）** — 02:01 重启导致 SSH 主机密钥重建，新指纹未带外确认 |

**阶段 A 未通过 → 不进入阶段 B/C。**

> 注：数据与站点本身健康（44 文件 / 117 549 350 B / nginx active / 证书未变），
> 阻断点只在"上传通道身份未确认"。确认并重固定后，A4 的月度用量读取也可恢复。
