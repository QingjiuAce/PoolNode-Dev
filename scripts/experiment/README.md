# scripts/experiment — 实验采集调度层（PN-RECOVERY-01）

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
├── runner.py     # 唯一入口：--task <name> / --smoke / --smoke-all / --inject <case>
├── health.py     # 心跳与失败检测（读状态目录，产出告警与 HEALTH.md）
└── README.md
```

## 五个任务

| 任务 | 正式脚本 | 采样窗口 | 输出 |
|---|---|---|---|
| `cmin` | `scripts/cmin_experiment.py` | 01/12/20/21/22/23 点 :15 | `reports/benchmarks/cmin-experiment/runs.jsonl` |
| `rcv05` | `scripts/bench_v05_pairs.py` | 20–23 点 :25 | `reports/benchmarks/v05-rc1/runs.jsonl` |
| `rcmatrix` | `Pool-investigate/scripts/rc_matrix.py` | 20–23 点 :35 | `reports/benchmarks/v05-chunk-queue/runs.jsonl` |
| `probe` | `scripts/peak_probe.py` | 12/16/20/21/22/23 点 :05 | `reports/benchmarks/v06-monitoring/local-probe.jsonl` |
| `sync` | `scripts/collect_box_probe.py` | 每日 23:40 | `reports/benchmarks/v06-monitoring/cn2box-probe.jsonl` |

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
├── alerts.current.json                   # 当前告警（覆盖写）
├── alerts.jsonl                          # 告警历史（仅状态变化时追加）
└── HEALTH.md                             # 人类可读面板
```

状态字段：`task_name / run_id / start_time / end_time / exit_code / status /
branch / commit / script_version / output_files / record_count / sha256_status /
upload_status / error_summary / validation`。

`SKIPPED` 与 `SUCCESS` 严格区分：窗口外、脚本自身输出 `skip:` 都会记为 SKIPPED。

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
