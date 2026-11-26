# 首轮 C_MIN 窗口 Runbook（授权后执行）

> 适用：`fix/experiment-scheduling-guard` 分支，worktree `Pool-guard`
> 目的：把"授权后怎么做"写成可照抄的步骤，避免临场改动参数或漏检查

## 0. 授权前提（缺一不可）

- 用户明确给出：**日期 + 窗口（如 12:15）+ 流量上限**
- 本次建议：2026-11-27 12:15（04:15 UTC），上限 120 MB
- ✅ **SSH 通道已修复并复验**（2026-11-27 02:26–02:28）：HK 主机密钥已按
  "线上 = 机内"重新固定，上传器改用 `id_ed25519_zcode` 密钥认证，
  `--task probe --smoke` 实测 `upload_status=ok`。
- ⚠️ CN2（`ssh hk`）的公钥已被移除，`PoolProbeSync` 23:40 会 fail-closed；
  这不影响本窗口（窗口只写香港收件箱），但需单独恢复。

## 1. 窗口前 30 分钟

```powershell
cd F:\鲸鲸\GitHub\开源二创\Pool-guard
git status --short --branch            # 必须干净（代码目录无改动）
python -X utf8 scripts\experiment\health.py
```

人工确认：

- [ ] 笔记本已接电源，电源计划不会休眠/合盖不睡眠
- [ ] 网络可用（打开一次 `https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg`）
- [ ] `HEALTH.md` 总体 OK，cmin 显示 SUSPENDED（预期）
- [ ] 磁盘剩余 > 10 GB
- [ ] 无其他人在跑测速/大流量下载

可选：在窗口前 10 分钟手工跑一次探针（会占 ~16 MB，且会持有链路锁 1 分钟）：

```powershell
python -X utf8 scripts\experiment\runner.py --task probe
```

> 只在探针档位小时内才会真正采样；否则记 SKIPPED（这是预期的）。

## 2. 解除挂起（三个开关，缺一不可）

1. **代码侧**：把 `TASKS["cmin"]["suspended"]` 改为 `False` 并提交（提交后工作区必须干净，
   否则正式运行的 `code_dirty` 门会拦下）；
2. **一次性授权文件**：`reports/experiment-logs/authorizations/cmin-2026-11-27T12.json`；
3. **任务侧**：`Enable-ScheduledTask -TaskName PoolCminExp`。

授权文件内容（严格按此格式）：

```json
{
  "task": "cmin",
  "date": "2026-11-27",
  "hour": 12,
  "minute": 15,
  "timezone": "Asia/Shanghai",
  "max_bytes": 120000000,
  "single_use": true,
  "approved_by": "<授权人>",
  "approved_at": "<ISO 时间>"
}
```

门控行为：缺文件 → `SKIPPED: no_authorization`；小时不匹配 → 同上；
同一窗口第二次 → `SKIPPED: authorization_already_consumed:<run_id>`；
计划流量 > `max_bytes` → `FAILED: budget_exceeds_authorization`（**不消费授权**）。

> 注意：`test_cmin_is_suspended_and_off_plan_hour_removed` 断言 `suspended is True`。
> 解除挂起时该用例必须同步改为断言 `False`（保留"hours 不含 1"的断言），
> 否则测试会拦下你——这是有意的防误改设计。

确认三个开关都就位：

```powershell
Get-ScheduledTask -TaskName PoolCminExp | Select-Object State
Get-ScheduledTaskInfo -TaskName PoolCminExp | Select-Object NextRunTime
Get-ChildItem reports\experiment-logs\authorizations\      # 必须有 cmin-2026-11-27T12.json
```

## 3. 窗口执行（12:15:00 自动开始）

不要手动启动。观察：

```powershell
Get-Content reports\experiment-logs\cmin\2026-11-27\<run_id>.log -Wait
```

记录（建议边跑边填）：

| 项 | 值 |
|---|---|
| 实际启动时间 | |
| run_id | |
| 锁（link/out） | |
| 已完成的 seq / 40 | |
| 当前 rep | |
| 遥测（TTFB / loss） | |
| 流量估算 | |
| 异常 / 重试 | |
| 退出码 | |

## 4. 出现以下情况立即停止（保留数据）

- 锁状态异常 / 输出目录被其他进程写
- 探针身份不明或时间不同步
- 链路出现无法解释的异常（丢包 100%、TTFB > 10 s 持续）
- 流量达到批准上限
- 状态与实际行为不一致（例如日志不动但任务仍 RUNNING 超过 20 分钟）
- 无法确认样本与 run_id 的对应关系

停止方式：`Stop-ScheduledTask -TaskName PoolCminExp`，**不要**删除任何数据；
状态保留为实际结果（PARTIAL/TIMEOUT/FAILED）。

## 5. 结束后检查（§5.3 十三项）

```powershell
$rid = "<run_id>"
Get-Content "reports\experiment-logs\state\cmin\$rid.json" | ConvertFrom-Json | Format-List
python -X utf8 - <<'PY'
import json, pathlib, collections
rows=[json.loads(l) for l in pathlib.Path("reports/benchmarks/cmin-experiment/runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
rid="<run_id>"
rs=[r for r in rows if r.get("run_id")==rid]
print("records", len(rs))
print("reps", sorted({r["rep"] for r in rs}))
print("pairs", collections.Counter((r["rep"], r["workers"]) for r in rs))
print("cmins per group ok", all(len({r["cmin"] for r in rs if r["rep"]==rep and r["workers"]==w})==2
                               for rep in range(5) for w in (1,8,13,16)))
print("all sha_ok", all(r.get("sha_ok") for r in rs))
print("unique arm_id", len({r["arm_id"] for r in rs})==len(rs))
print("seeds", {r.get("seed") for r in rs})
PY
```

分类：

```powershell
python -X utf8 scripts\experiment\window_class.py `
  --telemetry "reports\experiment-logs\telemetry\$rid.json" `
  --at "2026-11-27T12:15:00+08:00" `
  --out "reports\experiments\PN-EXPERIMENT-01\cmin-first-window\analysis\classification.json"
```

远端核对（逐文件 SHA）：上传日志里已有比对结果；如需复核，登录收件箱执行 `sha256sum`。

## 6. 收尾（无论成败，顺序执行）

1. `Disable-ScheduledTask -TaskName PoolCminExp`（立即，不等实验结束）；
2. **把 `suspended` 改回 `True`** 并提交（保证 20–23 点即使被谁启用也不会跑）；
3. **清除本次一次性授权标记**：把
   `authorizations/cmin-2026-11-27T12.json` 与 `…T12.consumed.json`
   移动到 `reports/experiments/PN-EXPERIMENT-01/cmin-first-window/manifests/`
   作为证据保留（**不删除**，但不再留在生效目录）；
4. 确认没有遗留进程：`Get-Process python` 中无本次 run；`locks.jsonl` 最后一条是 `released`；
5. 确认 `NextRunTime` 不再指向今天 20:15（任务已 Disabled）；
6. 提交实验数据与报告；不修改历史数据；
7. 不因为失败而调低阈值或删除异常样本。

## 7. 回滚（如果忘了改回）

```powershell
Disable-ScheduledTask -TaskName PoolCminExp
# 并恢复 runner.py 的 suspended: True，提交
# 授权文件反正已被 consumed 标记锁死，重复触发只会 SKIPPED
```
