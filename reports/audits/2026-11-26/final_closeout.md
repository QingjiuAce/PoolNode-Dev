# PoolNode 全面审计整改收口报告（2026-11-26）

> 分支：`feature/v0.6-alpha`
> 整改前 HEAD：`1e38122`
> 整改后 HEAD：`8bcf1e4`（本报告的提交紧随其后）
> 执行原则：生产冻结、不推送、不改历史、不伪造网络数据；所有结论以仓库与实测为准。

---

## 1. 执行摘要

本次整改**不以新增功能为目标**，只恢复工程可信度。已完成：

| 类别 | 结果 |
|---|---|
| P0-3 实验数据 | RC 健康窗/劣化窗 metrics 分别恢复归档（20 文件 × 24 行），`Metrics.dump` 改为排他创建 + run_id，不再覆盖 |
| P0-1 RC Harness | B 臂显式 `--scheduler static`、D 臂显式 `--scheduler auto`；运行后用客户端 metrics/JSON 校验实际策略；旧性能结论正式勘误 |
| P0-2 安全基线 | Alpha 定向移植 dev 的 guarded IO / system disk guard / Windows 根目录修复；未整分支合并 |
| P0-4 备份 | 阶段 0 + 整改后两份 Bundle，均校验通过；最终 Bundle 克隆后全量测试可复现 |
| 版本治理 | `0.6.0.dev1` 单一来源，CLI/UA 派生；四层发布边界写入文档 |
| P1 | CI 工作流、takedown 文案、origin 配置化、严格 bool、探针硬编码、flaky、文档、依赖 |
| P2 | 仓库卫生、死代码、fixture 一致性、benchmark 默认输入、审计并发、主机白名单测试、报告状态 |

**仍然开放**（不属于本次可单方面完成）：

1. 真实网络复测（健康窗 + 劣化窗各 5 组）未执行 → V0.5 RC **不具备重新验收条件**。
2. C_MIN 5 组背靠背复验仍在采样；01:15 档为旧配对顺序，不计入。
3. 异机/远程备份未做（需授权 push）。
4. CI 已建立但未在 GitHub 实际运行（未 push）。
5. 站长图片授权、ECDSA 指纹 VNC 核验等外部事项未变。

**总体结论：P0 整改完成，项目恢复为可验证、可追溯、可安全继续开发的状态。**

---

## 2. 分阶段执行记录

### 阶段 0：工作区盘点与灾难恢复准备

| 项 | 内容 |
|---|---|
| 修改前状态 | 11 个已跟踪文件修改（10 个 RC metrics 覆盖写 + C_MIN runs 追加 40 行），0 暂存，0 未跟踪 |
| 操作 | `git status/branch/tag/remote/log` 全量盘点；在仓库外建立 Bundle、工作区 diff、改动文件副本、`reports/benchmarks` 全量副本、SHA256 清单 |
| 备份 | `F:\鲸鲸\GitHub\开源二创\_pool-backup-2026-11-26\`；Bundle 18 refs，`7fd332b1…`；70 个文件 SHA |
| 还原验证 | 在 detached worktree 上 `git apply --check` 通过并实际还原，`metrics/1.jsonl` 逐字节一致 |
| 交付 | `reports/audits/2026-11-26/{repository_baseline,branch_inventory,backup_manifest}.md`、`backup_sha256.txt`、`raw/` |
| 未完成 | 仅本地备份；异机/远程未做（未授权） |

### 阶段 1：实验数据保全与 Metrics 持久化

| 项 | 内容 |
|---|---|
| 修改前状态 | 健康窗数据只在 HEAD；工作区被 23:25 劣化窗覆盖；`Metrics.dump` 使用 `w` |
| 操作 | 健康窗从 `git HEAD:...` 提取；劣化窗从工作区提取；分别写入 `recovered/{health,event}_window/`；随后把被跟踪 metrics 恢复为 HEAD 内容 |
| 校验 | 20/20 文件、每份 24 行、JSONL 可解析、SHA 与 `recovery_index.json` 一致；归档与来源逐字节一致 |
| 代码 | `Metrics.dump`：同目录临时文件 + fsync + `os.link` 排他创建；失败清理临时文件；每条记录带 `run_id`；`fetch --run-id`；目标已存在时在网络请求前失败 |
| Harness | C_MIN：两个 C_MIN 值背靠背配对、worker 组随机、`run_id`/`arm_id` |
| 测试 | 新增 `test_metrics_persistence.py`（7）与 `test_cmin_experiment.py`（3）；全量 **123 passed / 2 skipped** |
| 数据缺口 | 健康矩阵其余 80 臂、20/21/22 点各 10 臂的逐请求 metrics 在提交前已被覆盖，**不可恢复**；仅 `runs.jsonl` 汇总保留（如实记录，未补造） |

### 阶段 2：V0.5 RC Harness 策略修复

| 项 | 内容 |
|---|---|
| 修改前状态 | B 臂不传 `--scheduler` → 客户端默认 auto → 5.4MiB 实际 dynamic；B/D 实为 dynamic vs dynamic |
| 操作 | `ARM_SPECS` 显式声明 A/B/C/D；B 传 static，D 传 auto；`policy_ok` 由客户端 JSON/metrics 的 `scheduler` 与模式校验；metrics 文件按 run_id 唯一；manifest 追加 + run_id/arm_id；重叠运行 `SKIPPED_OVERLAP`；`fetch --json` |
| 勘误 | `ERRATA_2026-11-26.md`：撤回健康窗 ≤10%、P90 ≤15%、劣化窗 ≥30% 等全部旧结论；保留 SHA 完整性与同路径噪声对照（降级） |
| 复测计划 | `RETEST_PLAN_2026-11-26.md`：健康/劣化各 ≥5 组、同资产同并发、B/D 平衡、全量 metrics、SHA、单流 + CN2 对照、重叠标记 |
| 测试 | `test_bench_harness.py` 13 项，含“B 实际 dynamic 必须判失败”回归；全量 **136 passed / 2 skipped** |
| 未完成 | **真实网络复测未执行**；V0.5 RC 不重新验收 |

### 阶段 3：Alpha 安全基线恢复

| 项 | 内容 |
|---|---|
| 修改前状态 | `pool/` 树与 `v0.5.0-rc.1` 相同（`82966c4…`），缺 dev 的 guarded IO、系统盘护栏、Windows 根目录修复 |
| 操作 | 逐文件定向移植 dev 最终版：`_guarded_io.py`、`cli/config/storage/downloader/validator/permissions`、`config.example.yaml`、对应测试；**未 merge dev** |
| T1–T5 | alpha `be489a3` 与 release `884db00` 的 `range_transport.py` / `test_peak_download.py` diff 为空；无重复实现 |
| Windows | `probe != probe.parent` 修复；新增 4 项盘符根目录/文件/锚点回归测试 |
| 测试对比 | dev 隔离 worktree：**157 passed / 1 skipped**；alpha 移植后：**195 passed / 1 skipped**；差值 +38 全部为 alpha 独有测量/审计测试 |
| 跳过 | `test_paths.py:71`（Windows 创建符号链接需特权），不计为通过 |
| 未完成 | 无 P0 项；真实 mount 操作仍由模拟测试覆盖 |

### 阶段 4：本地工程资产保护

| 项 | 内容 |
|---|---|
| 操作 | 阶段 3 后 Bundle + 全部整改后 Bundle；均 `git bundle verify`；从 final bundle clone 并跑全量测试 |
| 中间检查点 | `pool-all-20261126-after-fix.bundle`，`b7070262…`，alpha `0b53e12` |
| 最终检查点 | `pool-all-20261126-final.bundle`，`82a80523…`，alpha `627cb43`；克隆后 **255 passed / 1 skipped** |
| 后续提交 | `8bcf1e4`（origin API 边界校验）与本报告提交在 final bundle 之后；收口时另建 closeout Bundle |
| 边界 | 本地备份；未 push；未异机 |

### 阶段 5：版本标识治理

| 项 | 内容 |
|---|---|
| 修改前状态 | 包版本 `0.1.0`，UA `pool-poc/0.1`，与 v0.4-beta/v0.5/v0.6 分支脱节 |
| 操作 | `pool/__init__.py` 单一来源 `0.6.0.dev1`；CLI `--version` 与 `downloader.user_agent()` 派生；新增 `docs/VERSION_POLICY.md`；README 版本表与发布边界 |
| 测试 | `test_version.py` 5 项；全量 **200 passed / 1 skipped** |
| 边界 | 未创建 Release、未移动 tag、未替换 v0.4-beta |

### 阶段 6：P1 级问题

| 编号 | 处置 | 状态 |
|---|---|---|
| P1-1 CI | `.github/workflows/ci.yml`：Ubuntu + Windows × Python 3.10–3.14，`compileall` + `pytest` | ✅ 文件已建；**未在 GitHub 实际运行**（未 push） |
| P1-2 takedown | `docs/ISSUE_DRAFT.md` 改为“手动 `pool-poc remove`，无自动联动、无 SLA” | ✅ |
| P1-3 域名 | 新增 `pool/origin.py`；fetch/transport/probe/bench/acceptance 支持 CLI/env，保留生产默认；严格 https/凭据/路径校验 | ✅ |
| P1-4 严格 bool | `guard_system_disk` 与 `allow_loopback_http` 均拒绝字符串/数字/null，仅接受 YAML 布尔 | ✅ 19 项测试 |
| P1-5 Windows | 阶段 3 已完成，纳入回归与 CI | ✅ |
| P1-6 探针硬编码 | collector 不再默认 IP/root；`--host/--user` 或 `POOL_CN2_*` 显式传入；路径 shell 转义校验 | ✅ |
| P1-7 flaky | 移除 `sleep(0.02)`，改为其他 chunk 实际服务的 Event；端口重绑有界重试；删除未用 `free_port` | ✅ |
| P1-8 文档 | 修复 5 处失效链接；测试数、22.17、5.40MB/5.15MiB 口径、22:48–23:25 启动时间统一 | ✅ |
| P1-9 依赖 | `requirements-dev.txt`；README 声明 Python ≥3.10；CI 覆盖 3.10–3.14；评估后未引入 build 系统/pyproject | ✅ |

### 阶段 7：P2 级问题

| 编号 | 处置 | 状态 |
|---|---|---|
| 7.1 仓库卫生 | `.review-tmp2/` 5 个测试残留已归档到仓库外并删除；`.gitignore` 的 `data/`、`tmp/` 收窄为根目录；无孤儿 `.pyc` | ✅ |
| 7.2 死代码 | 删除未引用 `validator.sha256_bytes`；`fetch.json` 现被 `--json` 使用；`log_dir` 被审计设备检查使用 | ✅ |
| 7.3 Fixture | `peak_probe.FIXTURES`、`bench.FILES`、`bench-assets.yaml` 三方一致性测试（因探针需独立部署，未强行抽公共模块） | ✅ |
| 7.4 Benchmark CLI | 默认 `--assets` 改为存在的 `config/bench-assets.yaml`；缺失时启动即报错；不再解析中文 `模式：`，改用 `--json` | ✅ |
| 7.5 Storage 并发 | 审计 JSONL 加线程锁 + 跨进程 sidecar 锁 + 单次 write + fsync；实测发现 4 线程 100 条曾丢失 4 条，修复后 100/100；暂存名随机 12 字节 | ✅ |
| 7.6 权限校验 | 主机精确匹配已存在；新增大小写、尾点、相似域名、路径前缀/相似后缀、非 443 端口测试；路径 suffix 语义保持（允许上游前缀） | ✅ |
| 7.7 脚本/报告 | smoke 与 public-acceptance 注明范围分工；C_MIN 报告状态改为真实进度 | ✅ |

### 阶段 8：完整测试与独立审查

| 项 | 结果 |
|---|---|
| 全量 pytest | `python -m pytest tests/ -q -rs` → **256 passed / 1 skipped** |
| 编译 | `python -m compileall -q pool scripts tests` → OK |
| CLI | `python -m pool --version` → `pool-poc 0.6.0.dev1` |
| 数据 | 20 份恢复档案 SHA/行数一致；被跟踪 metrics 与 HEAD 归一化后一致；46 份阶段 0 数据副本仅 5 类**有意**变化（见 §6） |
| 敏感信息 | 未发现密码/私钥/token；代码与脚本中无生产 IP，仅回环地址 |
| 分支差异 | `_guarded_io.py`、`cli.py` 与 dev 完全相同；其余差异仅为本日新增（origin/Metrics/审计锁/版本/死代码） |
| 独立复查 | 1 个只读审查任务；主模型逐项复验（见 §7） |

---

## 3. Git 变更记录

| 项 | 值 |
|---|---|
| 当前分支 | `feature/v0.6-alpha` |
| 修改前 HEAD | `1e38122` |
| 修改后 HEAD | `8bcf1e4`（本报告提交紧随其后） |
| 工作区 | 干净（0 修改 / 0 暂存 / 0 未跟踪，报告提交前） |
| 新增提交 | `233b96f`、`1cb0d74`、`7d08faa`、`0b53e12`、`8a947ad`、`3209811`、`bff112a`、`627cb43`、`8bcf1e4` |
| 本地独有 | `release/v0.5`、`v0.5.0-rc.1`、`dev`+14、alpha、investigate/* 均未推送 |
| 远程推送 | **无** |
| Tag | `v0.5.0-rc.1` 未移动（`24317ae → 0376444`） |

Bundle：

| 文件 | SHA-256 | HEAD |
|---|---|---|
| `pool-all-20261126.bundle`（阶段 0 现场） | `7fd332b1…` | `1e38122` |
| `pool-all-20261126-after-fix.bundle` | `b7070262…` | `0b53e12` |
| `pool-all-20261126-final.bundle` | `82a80523…` | `627cb43` |
| closeout Bundle（报告提交后创建） | 见外部 `CLOSEOUT_BUNDLE_SHA256.txt` | 本报告提交 |

---

## 4. 关键安全修复（调用链）

### 4.1 sync

```
cli.main → load_config（guard_system_disk 默认 True；仅显式 bool false 可关）
  → cmd_sync
    → check_permission（授权先于网络/文件副作用）
    → validate_source_url（https + 精确主机白名单）
    → resolve_safe（字符校验 + realpath 复核）
    → check_system_disk（storage_root/target.parent/tmp_dir/log_dir/records_file 父目录
        逐项 st_dev；不可确认或与系统盘同设备 → SystemDiskGuardError）
    → _check_storage_capacity（向上探测在盘符根停止）
    → audit_writer(preflight)（审计目录/文件预检）
    → guarded_staging（设备校验 → 逐级 O_NOFOLLOW 打开 → O_EXCL 创建 → fstat 身份）
    → download_to_file(stream)
    → check_format / sha256_file(stream)
    → publish → _publish_checked（再次设备校验、目标 regular/nlink=1、
        暂存身份复核、fsync、发布后 inode 复核）
    → record（审计锁 + fsync）
```

关键点：**所有设备与路径检查均在创建 staging 之前**；每个目录/文件在打开与替换前后用 `(st_dev, st_ino)` 复核，防止挂载点/符号链接在检查与写入之间被替换。

### 4.2 remove

```
cmd_remove → audit_writer（先持有并核验审计文件身份）
  → remove_file → validate_relative_path → 设备校验
  → 逐级 guarded 打开 → 目标 regular/同设备/nlink=1
  → root.verify_visible → before_unlink(审计身份核验) → unlink(dir_fd) → fsync
```

### 4.3 审计追加并发

- `_audit_file_lock`：先线程锁，再对 `<records>.lock` 取 OS 锁（POSIX `flock`；Windows `msvcrt` 有界重试）。
- `_write_audit_line`：`O_APPEND` 单次/循环 write + `fsync`。
- guarded `audit_writer` 使用同一锁，并保持已打开文件的身份核验。
- 回归证据：修复前 4 线程 ×25 条只落 96 条；修复后 100/100。

### 4.4 Windows 路径边界

`while not probe.exists() and probe != probe.parent`：盘符根目录的 `parent == self` 不再造成无限循环；新增 4 项测试覆盖根目录、文件路径、全不存在锚点。

### 4.5 配置类型

`guard_system_disk` / `allow_loopback_http` 使用 `isinstance(value, bool)`；`"false"`、`0`、`null`、列表均报 `ConfigError`，不再被 `bool()` 静默转成 True。

---

## 5. RC 证据修复

| 项 | 说明 |
|---|---|
| 原证据为何失效 | 旧 Harness 的 B 臂不传 `--scheduler`，客户端默认 auto；5.4MiB 下 auto→dynamic，B/D 实际同策略 |
| 恢复的历史数据 | 健康窗 10 份（HEAD blob）+ 劣化窗 10 份（工作区现场），分别归档，含 SHA/行数/时间范围/来源 |
| Harness 修复 | B=static、D=auto 显式传参；`policy_ok` 依据客户端 JSON/metrics；唯一 metrics 路径；追加 manifest；重叠跳过 |
| 本地验证 | 13 项策略测试；实际 CLI 参数与 fetch 输出均被断言 |
| 网络复测 | **未执行**；`RETEST_PLAN_2026-11-26.md` 已准备 |
| V0.5 状态 | **不具备重新验收条件**；旧结论全部撤回，不删除历史报告 |

---

## 6. 测试结果

### 6.1 最终命令与输出

```text
$ python -m pytest tests/ -q -rs
SKIPPED [1] tests\test_paths.py:71: Windows 创建符号链接需要特权
256 passed, 1 skipped, 1 warning in 16.90s

$ python -m compileall -q pool scripts tests
（无输出，退出码 0）

$ python -m pool --version
pool-poc 0.6.0.dev1
```

环境：Windows 10.0.26300 x64、Python 3.14.5、pytest 9.2.0、Git Bash。

### 6.2 测试基线对比

| 基线 | 结果 |
|---|---|
| `dev`（隔离 worktree） | 157 passed / 1 skipped |
| Alpha 移植安全基线后 | 195 passed / 1 skipped（+38 全部为 alpha 独有测试） |
| 最终 | 256 passed / 1 skipped |

### 6.3 新增测试分布

| 文件 | 数量 |
|---|---:|
| `test_metrics_persistence.py` | 7 |
| `test_cmin_experiment.py` | 3 |
| `test_bench_harness.py` | 15 |
| `test_windows_paths.py` | 4 |
| `test_version.py` | 5 |
| `test_origin.py` | 22 |
| `test_config_bools.py` | 19 |
| `test_storage_concurrency.py` | 1 |
| `test_fixture_consistency.py` | 2 |
| `test_collect_box_probe.py` 新增 | 3 |
| `test_permissions.py` 新增 | 6 |
| `test_system_disk_guard.py` 新增 | 1 |

跳过的唯一测试：Windows 创建符号链接需要特权；**未统计为通过**。

### 6.4 数据验证

| 检查 | 结果 |
|---|---|
| 恢复档案 20 份 | SHA 与索引一致，24 行/份 |
| 被跟踪 RC metrics | 10/10 与 HEAD 归一化后一致 |
| 阶段 0 的 46 份 benchmarks 副本 | 仅 3 份报告文档、1 份 cron.log、10 份 metrics 有**有意**变化；其余逐字节一致 |
| 原始 C_MIN / RC runs | 仅追加，未改写 |
| run_id / arm_id | 新数据唯一；01:15 旧数据无 run_id（如实记录） |

---

## 7. 独立复查

- 使用 1 个只读审查任务（未修改工作区），范围：Metrics 持久化、RC Harness 策略、安全调用链、审计锁。
- 主模型逐项对照代码与测试复验；审查结论与处置见下方（如有 blocker 将回填）。

| 复查项 | 结论 |
|---|---|
| Metrics 排他创建/失败清理/并发 | 通过（本地并发测试与代码路径复核） |
| Harness B/D 实际参数与输出校验 | 通过（命令行构造 + fetch 输出断言） |
| 安全调用链检查先于写入 | 通过（CLI → check_system_disk → staging → publish） |
| 审计锁顺序与 Windows 实现 | 通过（线程锁→OS 锁；100/100 测试） |
| 未覆盖项 | 真实网络、真实跨进程 SSH 采集、GitHub CI 运行 |

---

## 8. 遗留问题

### 需要用户授权

1. `git push` 任意分支/tag（当前全部本地）。
2. 异机/远程备份。
3. 创建 GitHub Release 或移动 tag。
4. 部署 V0.5 RC / V0.6 Alpha。

### 需要真实网络环境

1. V0.5 健康窗/劣化窗各 5 组 B/D 配对（按 `RETEST_PLAN`）。
2. C_MIN 5 组背靠背复验（12/20/21/22/23 点自动任务）。
3. RC 分块队列 A/B/C 矩阵（由 `PoolRCm` 在 `Pool-investigate` worktree 执行）。

### 需要人工判断

1. `endswith` 路径后缀是否继续允许上游前缀（当前保留，测试锁定）。
2. 是否引入 pyproject/打包体系（当前评估为不必要）。
3. 旧 RC 报告是否需要在 GitHub Issue/站内进一步说明（当前仅仓库勘误）。

### 未达到验收标准

1. **V0.5 RC 性能验收**：未执行真实复测，不能宣称通过。
2. **CI**：工作流已添加，但未在 GitHub 运行；无法证明远端执行成功。
3. **C_MIN 决策**：数据未齐，V0.6 Alpha 继续冻结。

---

## 9. 最终项目状态

| 项目 | 最终状态 |
|---|---|
| Server V0.2 | **保持不变**（未改 nginx/文件/白名单/DNS/BBR） |
| Client V0.4-beta | **保持当前发行状态**（未替换） |
| V0.5 RC | Harness 已修复、旧结论已勘误；**尚不具备重新验收条件**（缺真实复测） |
| V0.6 Alpha | **安全基线已恢复**；自适应算法未实现、未授权 |
| C_MIN | 具备继续实验条件；5 组复验采样中；01:15 旧顺序不计入 |
| Git 备份 | 本地：✅ 三份 Bundle；异机：❌；远程：❌ |
| P0 | **已完成**（A1–A10 见下） |
| P1 | **已完成**，CI 远端运行待授权 push |
| P2 | **已完成** |

### A1–A10

| 编号 | 验收项 | 状态 | 证据 |
|---|---|---|---|
| A1 | 数据备份 | ✅ 本地 | Bundle ×3、工作区 diff、46 份数据副本、SHA 清单 |
| A2 | Metrics 不覆盖 | ✅ | `Metrics.dump` 排他创建；7 项测试 |
| A3 | 双窗恢复 | ✅ | 20 份归档 + `RECOVERY_MANIFEST.md`；不可恢复范围已明示 |
| A4 | Harness B=static/D=auto | ✅ | `ARM_SPECS`、`policy_ok`、15 项测试 |
| A5 | RC 报告勘误 | ✅ | `ERRATA_2026-11-26.md` + 报告横幅 + closeout 注记 |
| A6 | 安全基线 | ✅ | guarded IO/系统盘护栏移植；系统盘 29 + 挂载 17 + 兼容 9 + T1–T5 26 = 81 项通过 |
| A7 | Windows 根目录 | ✅ | `probe != probe.parent` + 4 项测试 |
| A8 | 回归测试 | ✅ | 256 passed / 1 skipped；T1–T5 26 项 |
| A9 | 版本标识 | ✅ | `0.6.0.dev1` 单一来源 + 5 项测试 |
| A10 | 发布边界 | ✅ | 未 push/发布/部署/移动 tag；服务端 V0.2 未动 |

---

## 10. 附：本次整改的关键文件

- 审计：`reports/audits/2026-11-26/`（基线、分支、备份、安全移植、raw）
- 数据：`reports/benchmarks/v05-rc1/recovered/`、`ERRATA_2026-11-26.md`、`RETEST_PLAN_2026-11-26.md`
- 代码：`pool/origin.py`、`pool/range_transport.py`（Metrics）、`pool/storage.py`（审计锁）、`pool/_guarded_io.py`
- 工具：`scripts/bench_v05_pairs.py`、`scripts/cmin_experiment.py`、`scripts/peak_probe.py`、`scripts/collect_box_probe.py`
- CI：`.github/workflows/ci.yml`
