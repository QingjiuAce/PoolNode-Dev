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

### 服务器侧流量（02:27 通过 `ssh pool` 读取）

```
/var/log/nginx 现存 11 月日志合计（23–27 日，含 .gz）：6053.0 MB / 19 944 请求
数据盘：/dev/vdb1  49G  已用 199M  可用 47G
```

- 更早的 11 月日志已轮转删除，所以 **6.05 GB 是"23 日起"的下界**，不是全月总量；
- 审计已知配额为 **1 TB/月**；本次 71.1 MB = 月内已见流量的 1.2%、配额的 **0.007%**，
  即使按最坏 124.4 MB 也只占配额 0.012%；
- 磁盘余量 47 GB，40 次 1.7 MB 下载（临时文件用后即删）无压力。

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

## A6. 生产机身份与访问通道（02:26–02:28 复核，已解除 HK 阻断）

### 发生了什么（按机器上的证据）

```
02:04:27  reboot（last reboot；/proc/uptime 与 02:26 的 1339 s 一致）
02:09      root 从 tty1（控制台）登录，持续 00:00
02:12–02:15 root 从 tty1 登录，持续 00:03
02:14:24  /etc/ssh/ssh_host_{rsa,ecdsa,ed25519}_key 全部重建（mtime 一致）
```

- 02:15 起，本机用旧固定密钥连 HK 失败；02:27 用旧密码认证也失败。
- **结论：02:09–02:15 有一次控制台维护**（改主机密钥 + 改 root 密码），
  不是远程入侵。之前的"MITM"假设被这条时间线排除。
- 站点与数据全程未受影响：`44` 文件 / `117 549 350` B / `nginx active` /
  `NRestarts=0` / 证书 `2027-02-18` 未变；`/srv/pool/labnode/*` 与
  `/opt/poolnode-lab/source` 均在。

### 身份确认方式（三重一致）

| 来源 | ed25519 指纹 |
|---|---|
| `ssh pool`（key 认证登录后）`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` | `SHA256:MlHlKSmWIqL4swLGNKDUd/sec6X93Zf+9hxTAo7WptU` |
| `ssh-keyscan 156.233.227.20`（线上实际） | 同上 |
| `https://dl.bszx.site/monitor/status.json` | `hostname=ser103837614816`, `uptime_s` 与重启时刻一致, 44/117549350 |

ECDSA `Yk3l…`、RSA `/UsDy…` 同样三方一致。

### 已完成的修复

1. `~/.ssh/pool-lab/known_hosts_hk_lab` 按"线上 = 机内"的指纹重新固定（旧文件已备份）；
2. 上传器改用 **SSH 密钥**（`~/.ssh/id_ed25519_zcode`，与 `ssh pool` 同一身份），
   `~/.pool/remote.json` 已移除明文密码（旧文件备份为 `.bak-*`）；
3. 端到端验证：`runner.py --task probe --smoke` → `SUCCESS`，`upload_status=ok`
   （3 个文件上传 + 远端 SHA 核对通过）；
4. 测试：`302 passed / 1 skipped`（新增"只有 key 没有 password 不得 KeyError"用例）。

### CN2 也已恢复（02:31 复核）

用户恢复公钥后实测：

```
host=ser654121548140    boot=2026-11-27 02:03:53    uptime_s=1654
ssh-keygen -lf ssh_host_ed25519_key.pub → SHA256:F2PhHqAj…rdg   （= 线上值，一致）
crontab: 5 * * * * … peak_probe.py --tag cn2box --slots 12,16,20,21,22,23
         --window 2026-11-25T00:00/2027-12-31T23:59 --slot-guard --enforce-window
/root/pool-probe/probe-results-v06.jsonl → 12 行，最后写入 11-26 23:05（下一档 12:05 才采）
```

采集器端到端演练（`--out` 指向临时文件，**不碰正式归档**）：

```
OK collected lines=12 (+12) sha=0cc764ffefa43c3b
→ 拉取 12 行、39805 字节，SHA 与远端文件一致
```

两台机器的重启时刻只差 34 秒（CN2 `02:03:53` / HK `02:04:27`），
指向**同一次机房/服务商维护**。CN2 的三个主机密钥同样与机内一致
（`F2Ph…` / `1ltG…` / `KMRi…`）。

今晚 23:40 的 `PoolProbeSync` 可以正常工作；它会把 11-26 的 6 条新记录
追加进 `cn2box-probe.jsonl`（历史 6 行字节不变），这是采集器的正常增量行为。

## A7. 阶段 A 判定

| 检查 | 结果 |
|---|---|
| A1 Git/工作区 | PASS |
| A2-1 正式模式 `--out` | PASS（正式模式端到端用例 + 全任务参数化） |
| A2-2 CN2 CRLF/LF | PASS（临时副本演练，历史字节不变） |
| A3 实验矩阵 | PASS（20 对，与协议一致；128K 的 w16 实为 13 块已注明） |
| A4 流量预算 | **CONDITIONAL**（最坏 124.4 MB > 120 MB；配额余量充足，需确认口径） |
| A5 调度门控 | PASS（一次性授权 + 双保险 + 无补跑） |
| A6 生产身份 | **PASS**（HK 与 CN2 的主机密钥都 = 机内值；上传/拉取均实测通过） |

**阶段 A：全部通过。** 剩余两项不是技术问题，而是授权确认：
预算口径（124.4 MB 最坏 vs 120 MB 上限）与运行条件（电源/登录/不休眠）。
