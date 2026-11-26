# 阶段 1-2 — 资源互斥实现与验收（PN-EXPERIMENT-01 §2.4–§2.6）

> 分支：`fix/experiment-scheduling-guard`（独立 worktree `Pool-guard`，基于恢复基线 `0d1a522`）
> 提交：`e7f6906`
> 结论：**PASS**（任务书 §2.4 的 10 条要求逐条落实；§2.6 的五类测试齐全）

## 1. 资源模型

按**实际依赖**划分，不做全局大锁：

| 资源 ID | 含义 | 任务 |
|---|---|---|
| `link:local-hk` | 本机 ↔ 香港（下载测速 + 回传上行） | cmin, rcv05, rcmatrix, probe, sync |
| `out:cmin-experiment` | `reports/benchmarks/cmin-experiment/` | cmin |
| `out:v05-rc1` | `reports/benchmarks/v05-rc1/` | rcv05 |
| `out:v05-chunk-queue` | `reports/benchmarks/v05-chunk-queue/` | rcmatrix |
| `out:v06-monitoring` | `reports/benchmarks/v06-monitoring/` | probe, sync |
| `ssh:cn2` | 到 CN2 探测机的控制通道 | sync |

`health` 不取任何锁：它只读状态文件、写独立的 `HEALTH.md`/`alerts*`，不碰测速链路。
（任务书的"只有在确认不影响测试结果时才允许并行"——健康检查不产生网络流量。）

`sync` 也取 `link:local-hk`：它的 CN2 拉取走的是同一条本机上行，回传目的地又同在
香港，属于"与正式实验共用网络资源"，因此按第 3 条要求不得并行。

## 2. §2.4 十条要求逐条对照

| # | 要求 | 实现 | 证据 |
|---|---|---|---|
| 1 | 同一链路不得并行正式实验 | `link:local-hk` 独占 | `test_second_holder_gets_conflict` |
| 2 | 同一输出目录不得并行写入 | `out:<dir>` 独占 | 同上 + 每任务 `resources` 定义 |
| 3 | Sync 不得与实验或回传争用 | sync 取 `link:local-hk` + `ssh:cn2` | 资源表 |
| 4 | 手动与计划任务共用同一套锁 | 两者都只经 `runner.execute()`；锁目录是**机器级** `%LOCALAPPDATA%\PoolNode\locks` | 实测双进程争用（§4） |
| 5 | HealthCheck 可并行 | 不取锁、不产生流量 | `health.py` 只读状态 |
| 6 | 冲突写入独立运行记录 | 状态 JSON 记 `SKIPPED` + `error_summary`；另追加 `locks.jsonl` 的 `conflict` 记录 | `locks.py:_append_journal` |
| 7 | 冲突标记 SKIPPED，不算成功样本 | `finish("SKIPPED", ...)`，退出码 3，`record_count=0` | 实测（§4） |
| 8 | 不自动排队、不悄悄推迟窗口 | 只做**非阻塞**尝试，无等待、无重试 | `msvcrt.LK_NBLCK` / `LOCK_NB` |
| 9 | 无死锁、无重复释放、无永久残留 | 按 resource_id 排序非阻塞获取；`release()` 幂等清空 | `test_partial_acquisition_releases_everything`、`test_release_is_idempotent_and_journaled` |
| 10 | 不能只看锁文件年龄判断失效 | 锁由内核持有，进程死亡即释放；元数据只用于报告，不参与判定 | `test_lock_released_when_holder_process_dies`、`test_stale_owner_metadata_never_blocks` |

锁记录字段（`locks.jsonl`）：`lock_id / resource_id / task / run_id / pid / host /
acquired_at|at / state(acquired|conflict|released) / holder / holder_metadata`。

## 3. 并发与恢复语义

```
acquire(resources):
  for r in sorted(set(resources)):        # 固定顺序 → 无环路
      fh = open(<r>.lock, "a+b")
      if not try_lock(fh):                # 非阻塞
          record conflict; release_all(); raise LockConflict
      write <r>.owner.json (tmp+replace)  # 元数据，仅供报告
      journal(acquired)
```

- `release_all()` 逆序解锁并逐个 `journal(released)`；重复调用不再写记录。
- 进程被强杀（`os._exit`）时内核关闭句柄 → 锁自动释放；下一次运行**立即**可得，
  不需要超时、不需要 `stale` 判定。
- 元数据可能指向已死进程（被杀时来不及清），但因为它不参与判定，
  冲突报告里会写 `holder_metadata: present/unknown`，不会阻塞。

## 4. 验收证据

### 4.1 单元测试（新增 29 例）

```
$ python -m pytest tests/test_experiment_scheduling.py -q
29 passed
```

覆盖：并发冲突、异常退出释放、部分获取回滚、幂等释放、陈旧元数据、
非法资源名、`--out` 绑定（5 个任务参数化）、`--out` 解析、rcmatrix 不写他仓、
cmin 挂起、锁冲突 → SKIPPED、CRLF 前缀、真实分歧拒绝、种子复现、
安装脚本入口一致性、窗口分类 5 种判定。

### 4.2 全量回归

```
$ python -m pytest tests/ -q
297 passed, 1 skipped
```

相对恢复基线 `268 passed / 1 skipped` **+29 passed**，正好等于新增用例数；
无既有用例被修改或删除。

### 4.3 真实双进程争用（非模拟）

```
$ python runner.py --task probe --smoke          # 后台，持有 link:local-hk
$ python runner.py --task cmin  --smoke
{
  "task_name": "cmin",
  "status": "SKIPPED",
  "exit_code": 3,
  "record_count": 0,
  "upload_status": "not_attempted",
  "error_summary": "lock_conflict: link:local-hk held_by probe/probe-20261127T011744-1228e3 pid=44928"
}
cmin_exit=3
```

- 被拒的一方没有产生任何记录、没有回传、退出码 3。
- probe 侧正常完成（`records+1`、`upload_status: ok`）。
- 冲突原因写清了持有者的 task/run_id/pid。

### 4.4 计划任务链路（新代码）

`PoolSmokeTest`（Task Scheduler 触发，01:14:36–01:15:26）：

| 任务 | 结果 | 记录 | 耗时 | 锁 |
|---|---|---:|---:|---|
| cmin | SUCCESS | 4 | 6.8 s | link:local-hk, out:cmin-experiment |
| rcv05 | SUCCESS | 2 | 11.4 s | link:local-hk, out:v05-rc1 |
| rcmatrix | SUCCESS | 3 | 7.1 s | link:local-hk, out:v05-chunk-queue |
| probe | SUCCESS | 1 | 18.3 s | link:local-hk, out:v06-monitoring |
| sync | SUCCESS | 12 | 5.6 s | link:local-hk, out:v06-monitoring, ssh:cn2 |

每个资源都有配对的 `acquired` / `released` 记录（`locks.jsonl`）。

## 5. 代码变更清单

| 文件 | 变更 |
|---|---|
| `scripts/experiment/locks.py` | 新增：跨进程锁、持有者元数据、锁日志 |
| `scripts/experiment/runner.py` | 资源模型、加锁/冲突→SKIPPED、`--out` 注入、遥测调用、cmin 挂起与窗口修正 |
| `scripts/experiment/net_telemetry.py` | 新增：DNS/TCP/TLS/TTFB/ICMP 只读遥测 |
| `scripts/experiment/window_class.py` | 新增：证据驱动窗口分类 |
| `scripts/experiment/health.py` | `SUSPENDED` 状态、锁冲突可见性 |
| `scripts/experiment/install_tasks.ps1` | 纳入 `PoolSmokeTest`；默认保持 C_MIN 禁用；`-EnableCmin` 显式开关 |
| `scripts/collect_box_probe.py` | CRLF/LF 归一化前缀比较（只追加尾部） |
| `scripts/cmin_experiment.py` | 显式随机种子 + `seed`/`seq` 字段 |
| `tests/test_experiment_scheduling.py` | 新增 29 例 |

## 6. 未覆盖范围（如实声明）

| 项 | 说明 |
|---|---|
| 跨机器锁 | 本机锁无法约束 CN2 探测机与广州探针上的进程；与它们的竞争**只靠时间隔离**（§2.4 末段要求） |
| 同分钟探针 | 本机 probe 与 CN2 probe 都在 `:05`，同打一个 origin；无法互斥，只能作为残余风险评估（见调度审查报告） |
| 绕过 runner 直跑脚本 | 直接执行 `python scripts/cmin_experiment.py` 不取锁；任务书 §5.1 已要求正式实验必须走任务入口，本轮未增加文件系统级防护 |
