# PN-RECOVERY-01 最终恢复报告

> 执行时间：2026-11-27 00:05–00:36 CST
> 分支：`fix/experiment-pipeline-recovery`（基于 `feature/v0.6-alpha@4cb8b04`）
> 提交：`d889c18`（本轮实现）；`4cb8b04`（基线）
> 工作区：`F:\鲸鲸\GitHub\开源二创\Pool-experiment`（专用 worktree，未推送）

---

## 一、任务书 §十四 的十一个问题

| # | 问题 | 回答 |
|---|---|---|
| 1 | 五个任务是否全部恢复？ | **是**。`PoolCminExp / PoolRCv05 / PoolRCm / PoolProbeV05 / PoolProbeSync` 全部改指向 `scripts/experiment/runner.py`，触发器与账户保留；另新增 `PoolHealthCheck`（每小时 :50）与 `PoolSmokeTest`（手动烟测） |
| 2 | 是否彻底移除 Temp 依赖？ | **是**。脚本、日志、状态、烟测输出全部在仓库/`reports` 下；`%TEMP%` 无任何引用（`grep -r '%TEMP%\|AppData\\Local\\Temp' scripts/experiment/` 为空） |
| 3 | 是否验证了 Task Scheduler 真实执行？ | **是**。三轮均由 `Start-ScheduledTask -TaskName PoolSmokeTest` 触发；正式任务 `PoolRCv05` 在 00:25 被自身触发器启动并正确记 SKIPPED |
| 4 | 是否验证数据回传与 SHA 闭环？ | **是**。41 个文件（284 KB）回传到 `/srv/pool/labnode/experiment-inbox/smoke/`，逐文件远端 `sha256sum` 比对一致；幂等（同内容跳过、异内容报错） |
| 5 | 是否能够及时发现任务失败？ | **基本可以，但有缺口**。预检/执行/校验三层 + `health.py`（连续失败、错过窗口、stale RUNNING）+ 非零退出码；**缺外部通知渠道**，需人查看 `HEALTH.md` |
| 6 | C_MIN 是否具备正式启动条件？ | **具备**（管道层面）。但需用户确认窗口与预算；数据尚未采集 |
| 7 | v0.5 A/B/C 是否具备启动条件？ | **具备**（管道层面）。`rcmatrix` 依赖 `Pool-investigate` worktree 存在 |
| 8 | 是否存在未解决的采集风险？ | 有 4 项，见 §四 |
| 9 | 是否修改了生产环境？ | **否**。未动 nginx/证书/DNS/防火墙/`/srv/pool/data`；回传只写 `labnode` 下新目录 |
| 10 | 是否修改了任何历史实验数据？ | **否**。`git diff --stat reports/benchmarks/` 为空；历史 JSONL 未追加、未覆盖 |
| 11 | 是否解除 v0.6 Alpha 冻结？ | **否**。冻结保持，未改 `feature/v0.6-alpha` |

## 二、交付物

| 项 | 路径 |
|---|---|
| 阶段 0 盘点 | `reports/recovery/phase-0_inventory.md` |
| 阶段 1 管道恢复 | `reports/recovery/phase-1_pipeline_recovery.md` |
| 阶段 2 失败检测 | `reports/recovery/phase-2_failure_detection.md` |
| 阶段 3 烟测 | `reports/recovery/phase-3_smoke_test.md` |
| 阶段 3 证据 | `reports/recovery/phase-3_evidence.json`（28 run 状态 + 34 JSONL SHA） |
| 阶段 4 实验计划 | `reports/recovery/phase-4_experiment_plan.md` |
| 任务快照（可回滚） | `reports/recovery/task-snapshots/*.xml`（5 份） |
| 代码 | `scripts/experiment/{runner.py,health.py,install_tasks.ps1,README.md}` |
| 改动 | `scripts/collect_box_probe.py`（+`--out/--log`）、`.gitignore` |

## 三、变更清单与分支差异

```
 .gitignore                        |   3 +
 scripts/collect_box_probe.py      |   8 +-
 scripts/experiment/README.md      |  86 +++++
 scripts/experiment/health.py      | 158 ++++++++++
 scripts/experiment/install_tasks.ps1 | 74 +++++
 scripts/experiment/runner.py      | 520 +++++++++++++++++++++++++++++++++++
 reports/recovery/*.md/json        | 6 files
```

相对基线 `4cb8b04` 的差异仅限上述文件；`pool/`、`tests/`、`config/`、`reports/benchmarks/` 均无改动。

## 四、剩余风险（未解决）

| # | 风险 | 级别 | 说明 | 建议 |
|---|---|---|---|---|
| R1 | **无外部告警渠道** | P1 | 失败只落盘到 `HEALTH.md`/`alerts.jsonl`，需人查看 | 接一条 webhook 或邮件；数据已是结构化 JSON，成本低 |
| R2 | 回传凭据为明文文件 | P2 | `%USERPROFILE%\.pool\remote.json`（ACL 仅本人）；服务器仅开密码认证 | 后续改为专用 SSH key（需改服务器 `authorized_keys`，属生产变更，需授权） |
| R3 | `PoolRCm ↔ rc_matrix.py` 映射为推断 | P2 | 依据：触发分钟 :35 与任务书四档一致、仓库中无第二个矩阵脚本 | 首次正式运行后检查 `v05-chunk-queue/runs.jsonl` 是否符合预期；不符则改 `TASKS["rcmatrix"]["script"]` |
| R4 | `PoolHealthCheck` 只每小时检查 | P3 | 最迟 1 小时发现失败 | 可改为任务结束后同步调用 `health.py` |
| R5 | 广州探针主机身份未核实 | P2（既有） | 仅见 8083 上报数据 | 独立任务核实，本轮未动 |
| R6 | 大陆探测机 `114.132.172.218` 未接入 | P3 | 目前只有服务器日志侧观测 | 待确认其归属与采集方式 |

## 五、烟测证据摘要

| 轮次 | 时间 | 结果 |
|---|---|---|
| R1 | 00:21:47 | cmin PARTIAL（上传同名冲突）、rcmatrix FAILED（字段名错）、sync FAILED（丢参数）→ 修 3 处 |
| R2 | 00:25:39 | **5/5 SUCCESS，全部上传 ok** |
| R3 | 00:28:58 | **5/5 SUCCESS**（可重复） |

10 项故障注入全部符合设计，无伪成功：

```
missing_script → FAILED     bad_interpreter → FAILED(spawn)
missing_dep    → FAILED     unwritable_out  → FAILED(EACCES)
offline        → FAILED     remote_down     → PARTIAL(connect_failed)
bad_jsonl      → FAILED     sha_mismatch    → FAILED(sha_fail)
timeout        → TIMEOUT    duplicate_run_id→ 第二次 FAILED
```

## 六、容量与状态

- 回传体积：284 KB（三轮）；正式实验预估首日 ~0.9 GB（见 phase-4 §6），在 3 GB 预算内。
- 任务错峰：:05 probe / :15 cmin / :25 rcv05 / :35 rcmatrix / :40 sync / :50 health，
  同一小时无重叠。

## 七、下一步（需用户确认后执行）

1. 确认 C_MIN 档位（三档 or 五档）与预算；
2. 确认 A/B/C 四档是否按 20:35–23:35 执行；
3. 首次正式运行后检查：`reports/experiment-logs/HEALTH.md` 与首个成功状态；
4. 若 `rcmatrix` 首次运行产出不符合 `v05-chunk-queue` 结构，立即停止并回报（R3）；
5. 其余（Node Monitor 入库、运营 timer、配置备份）按任务书 §十三 作为后续独立任务。

## 八、声明

本轮**未**修改生产下载服务、未改 DNS/证书/防火墙、未 reload/restart nginx、
未同步/发布/删除生产文件、未修改 40 张 PNG、未改任何历史实验数据与 Manifest、
未推送远端、未合并分支、未解除 v0.6 Alpha 冻结。

唯一的环境写入：五个计划任务的 Action 重指（原 XML 已快照）、新增两个计划任务、
`%USERPROFILE%\.pool\remote.json`（回传凭据，ACL 限本人）。
