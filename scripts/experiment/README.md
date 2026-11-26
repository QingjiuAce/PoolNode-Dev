# scripts/experiment — 实验采集调度层（PN-RECOVERY-01 / PN-EXPERIMENT-01）

> 目的：把"定时任务偶尔能跑"变成"每次执行都有记录、每次失败都能发现、
> 每份数据都能验证"。**实验逻辑不在这里**——这里只做调度、预检、日志、
> 状态、结果校验和回传；测量本身仍由 `scripts/*.py` 的正式脚本完成。

## 为什么存在

2026-11-26 07:15 之后，五个实验任务因为脚本放在 `%TEMP%\pool-probe\` 而被系统清理，
持续空跑失败 17 小时无人发现。本目录替代那套临时脚本，并且：

- 位于版本库内（不依赖 Temp）；
- 每次运行写独立 `run_id` / 日志 / 状态 JSON；
- 预检失败、超时、无新增记录、校验失败都能被识别（不会伪成功）；
- 结果可回传到云端实验收件箱并做 SHA 核对（幂等）。

## 结构

```
scripts/experiment/
├── runner.py         # 唯一入口：--task <name> / --smoke / --smoke-all / --inject <case>
├── locks.py          # 跨进程资源互斥（OS 级锁，非阻塞，异常退出自动释放）
├── net_telemetry.py  # 窗口分类用链路遥测（DNS/TCP/TLS/TTFB/ICMP，只读）
├── window_class.py   # 证据驱动的窗口分类（HEALTHY/DEGRADED/EVENT_TAIL/UNKNOWN）
├── health.py         # 心跳与失败检测（读状态目录，产出告警与 HEALTH.md）
├── install_tasks.ps1 # 重指/注册计划任务（默认保持 C_MIN 禁用）
└── README.md
```

## 五个任务

| 任务 | 正式脚本 | 采样窗口 | 输出 |
|---|---|---|---|
| `cmin` | `scripts/cmin_experiment.py` | 12/20/21/22/23 点 :15（**需授权**） | `reports/benchmarks/cmin-experiment/runs.jsonl` |
| `rcv05` | `scripts/bench_v05_pairs.py` | 20–23 点 :25 | `reports/benchmarks/v05-rc1/runs.jsonl` |
| `rcmatrix` | `Pool-investigate/scripts/rc_matrix.py` | 20–23 点 :35 | `reports/benchmarks/v05-chunk-queue/runs.jsonl` |
| `probe` | `scripts/peak_probe.py` | 12/16/20/21/22/23 点 :05 | `reports/benchmarks/v06-monitoring/local-probe.jsonl` |
| `sync` | `scripts/collect_box_probe.py` | 每日 23:40 | `reports/benchmarks/v06-monitoring/cn2box-probe.jsonl` |

`cmin` 在 `runner.TASKS` 里带 `suspended: True`：即使计划任务被重新启用，
正式模式也只会记 `SKIPPED: suspended...`，不会产生任何实验流量。
解除挂起 = 获得明确窗口授权后改这一个标志（并 `install_tasks.ps1 -EnableCmin`）。

## 资源互斥（PN-EXPERIMENT-01 §2.4）

| 资源 | 谁需要 |
|---|---|
| `link:local-hk` | 所有下载类任务 + `sync`（回传目的地同在香港） |
| `out:<目录>` | 写该输出目录的任务 |
| `ssh:cn2` | `sync` 的 CN2 控制通道 |

- 锁是**非阻塞**的：拿不到立刻放弃，记 `SKIPPED: lock_conflict: ...`，
  不排队、不推迟窗口（§2.4 第 8 条）。
- Windows 用 `msvcrt.locking`、POSIX 用 `fcntl.flock`；进程异常退出时由内核
  释放，**不靠锁文件年龄判断失效**，也不会留下永久残留锁（第 9/10 条）。
- 锁目录是**机器级**的（Windows `%LOCALAPPDATA%\PoolNode\locks`），
  因此不同 worktree 的手动运行与计划任务共用同一把锁（第 4 条）。
- 每次 `acquired / conflict / released` 追加到
  `reports/experiment-logs/locks.jsonl`，与正式实验样本分开（第 6 条）。
- 按 resource_id 排序后依次获取且全部非阻塞，不存在环路等待。

手动与计划任务的唯一差别是入口参数；两者都走 `execute()`，锁行为一致。

## 输出路径绑定

`execute()` 统一给子脚本注入 `--out`：`file` 型传文件路径，`dir` 型传目录。
任务定义里不再各自写 `--out`，避免"脚本写一处、校验读另一处"。

## 窗口分类

```powershell
python -X utf8 scripts\experiment\net_telemetry.py --out reports\experiment-logs\telemetry\x.json
python -X utf8 scripts\experiment\window_class.py --telemetry reports\experiment-logs\telemetry\x.json
```

阈值写死在 `window_class.py` 顶部（5 Mbps / 丢包 20% / TTFB 3000 ms），
在跑实验之前声明；证据不足或吞吐与链路指标冲突时返回 `UNKNOWN`。

## 运行

```powershell
# 正式（由计划任务调用；窗口外自动 SKIPPED）
python -X utf8 scripts\experiment\runner.py --task cmin

# 冒烟（跳过窗口门，输出到 reports/experiment-logs/smoke/<run_id>/）
python -X utf8 scripts\experiment\runner.py --task cmin --smoke
python -X utf8 scripts\experiment\runner.py --smoke-all

# 故障注入（只写 smoke 目录，验证不会伪成功）
python -X utf8 scripts\experiment\runner.py --inject missing_script

# 健康检查
python -X utf8 scripts\experiment\health.py
```

退出码：`0` SUCCESS、`1` FAILED、`3` SKIPPED、`4` PARTIAL、`5` TIMEOUT。

## 状态与日志

```
reports/experiment-logs/
├── <task>/<YYYY-MM-DD>/<run_id>.log      # 每次运行一份，互不覆盖
├── state/<task>/<run_id>.json            # 机器可读状态（见下）
├── smoke/<run_id>/                       # 烟测专用输出（与正式数据隔离）
├── telemetry/<run_id>.json               # 正式实验前的链路遥测
├── locks.jsonl                           # 锁的 获取/冲突/释放 记录
├── alerts.current.json                   # 当前告警（覆盖写）
├── alerts.jsonl                          # 告警历史（仅状态变化时追加）
└── HEALTH.md                             # 人类可读面板
```

状态字段：`task_name / run_id / start_time / end_time / exit_code / status /
branch / commit / script_version / output_files / record_count / sha256_status /
upload_status / error_summary / validation / locks / code_dirty`。

`SKIPPED` 与 `SUCCESS` 严格区分：窗口外、脚本自身输出 `skip:`、锁冲突都会记为 SKIPPED。

远端收件箱里的状态文件是**上传前**的快照（`upload_status: pending`）；
回传结果以上传后本地重写的状态文件与日志里的 `[UPLOAD]` 行为准。
上传器是"同名同内容跳过、同名异内容报错"，因此不重复上传同一个文件。

## 回传（可选）

若存在 `%USERPROFILE%\.pool\remote.json`（NTFS ACL 限当前用户）：

```json
{"host": "156.233.227.20", "port": 22, "user": "root", "password": "***",
 "known_hosts": "C:\\Users\\<you>\\.ssh\\pool-lab\\known_hosts_hk_lab"}
```

则每次运行把日志、状态与数据文件上传到
`/srv/pool/labnode/experiment-inbox/<task>/<date>/`，逐文件比对 SHA-256；
同名同内容跳过（幂等），同名不同内容直接报错（不覆盖）。

凭据缺失时状态为 `PARTIAL` 且 `upload_status=skipped:no-config`——本地采集不受影响。

## 约束

- 只写 `reports/` 下的实验目录；不碰 `/srv/pool/data`、不改生产配置。
- `rcmatrix` 引用 `Pool-investigate` worktree 的脚本（实验代码单一来源）；
  该 worktree 缺失时预检直接 FAILED，不会静默跳过。
  输出目录始终在本仓库内（`--out` 由 `execute()` 注入），**不会写进该 worktree**。
- 本仓库 `core.autocrlf=true`：被跟踪的 JSONL 会以 CRLF 检出。`collect_box_probe`
  因此对"前缀"做按行归一化比较，但只追加远端尾部字节，历史字节不改。
- 锁只在同一台机器内有效；CN2 探测机与广州探针的进程无法被本机锁约束，
  与它们的竞争一律靠时间隔离（正式窗口与 :05 探针错开 10 分钟）。
