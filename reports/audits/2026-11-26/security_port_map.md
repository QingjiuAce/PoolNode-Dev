# Alpha 安全基线定向移植记录（阶段 3）

> 目标：把 `feature/v0.6-alpha` 恢复到包含已批准安全加固的代码基线。
> 方式：**逐文件定向移植** dev 的最终版本（不是整分支 merge）。
> 结果：`pool/` 与 dev 的差异仅剩本日 Metrics 持久化改动。

## 1. 提交对应关系

| dev 提交 | 内容 | 处置 | 说明 |
|---|---|---|---|
| `fe03d54` | 代码边界澄清 + `tests/test_compatibility.py` | ✅ 移植最终文件 | 安全加固的前置重构，行为等价 |
| `7ac2621` | `guard_system_disk` 默认开启 + 系统盘测试 | ✅ 移植 | 核心安全默认值 |
| `62b0cb8` | sshd host-key 事件 + 性能文档 | ⬜ 未移植 | 纯运维/历史文档，不属安全基线 |
| `70411f6` | guarded IO + 原子发布/审计加固 + mount race 测试 | ✅ 移植 | 核心安全实现 |
| `884db00` | T1–T5 支撑修复 | ✅ 已存在 | alpha `be489a3` 与其实质等价（diff 为空） |
| `404cb33` / `36c45b7` / `9b53e7c` | RC 文档与测试数勘误 | ✅ 已有等价 | alpha 侧已有同名文档/测试 |
| `75e65b1` | release/v0.5 → dev 合并提交 | ⬜ 不移植 | 合并提交本身无内容 |

## 2. 文件清单

新增：

- `pool/_guarded_io.py`（147 行）：目录句柄、身份核验、`O_NOFOLLOW`、设备边界。

替换为 dev 最终版：

- `pool/cli.py`：Windows 盘符根目录终止条件；安全预检顺序；审计前置。
- `pool/config.py`：`guard_system_disk` 默认 `True`；非布尔值拒绝。
- `pool/storage.py`：`guarded_staging`、`_publish_checked`、guarded remove、`audit_writer`。
- `pool/downloader.py`：支持写入已打开的文件描述符（暂存文件）。
- `pool/validator.py`：`check_format` / `sha256_file` 接受文件对象。
- `pool/permissions.py`：与 dev 一致的授权解析。
- `config/config.example.yaml`：显式 `guard_system_disk: true`。

测试：

- 新增 `tests/test_compatibility.py`（9 项）、`tests/test_mount_race.py`（17 项）、
  `tests/test_system_disk_guard.py`（28 项）。
- 更新 `tests/test_downloader.py`、`tests/conftest.py`。
- 新增 `tests/test_windows_paths.py`（4 项，补 dev 缺失的盘符根目录回归）。

未改动：`pool/range_transport.py`（T1–T5 与 dev 实质一致；本日只叠加 Metrics
持久化与 run_id）、`pool/fetch.py`（本日只叠加 `--run-id` / `--json`）。

## 3. 关键调用链（从 CLI 到写入）

### 3.1 sync

```
pool.cli.main
  └─ _load_all → load_config（guard_system_disk 严格 bool，默认 True）
  └─ cmd_sync
       ├─ get_asset / check_permission        授权先于任何网络与文件副作用
       ├─ validate_source_url                 https + 主机白名单
       ├─ resolve_safe(storage_root, rel)     字符校验 + realpath 复核
       ├─ check_system_disk(cfg, target)      ← 在任何 staging 之前
       │    ├─ resolve(storage_root / tmp_dir)；拒绝 tmp 位于公开目录内
       │    ├─ _check_write_devices({
       │    │     storage_root, target.parent, tmp_dir,
       │    │     log_dir, records_file.parent, records_file 目标父目录 })
       │    │   └─ _directory_device → _existing_directory → stat().st_dev
       │    └─ 任一设备不可确认 / 与系统盘同设备 → SystemDiskGuardError（fail closed）
       ├─ _check_storage_capacity              向上探测终止于盘符根（不再死循环）
       ├─ audit_writer(preflight=True)         审计目标预检，失败即拒绝
       ├─ guarded_staging(cfg, asset_id)
       │    ├─ _directory_device(tmp_dir) != 系统盘设备
       │    ├─ directory(tmp_dir, device, create=True)
       │    │    └─ 逐级 O_NOFOLLOW / O_DIRECTORY 打开；创建前核对父目录设备
       │    └─ parent.open(name, O_CREAT|O_EXCL) + fstat 身份核验
       ├─ download_to_file(..., destination=stage.stream)
       ├─ check_format(stream) / sha256_file(stream)
       └─ publish(cfg, stage, relative_path)
            └─ _publish_checked
                 ├─ validate_relative_path + check_system_disk（再次）
                 ├─ 暂存与公开根必须同设备且非系统盘
                 ├─ directory(storage_root) + directory(final.parent)
                 ├─ 目标若存在：必须 regular / nlink=1 / 同设备，否则拒绝
                 ├─ stage.verify()（父目录身份 + 文件 inode 未变）
                 ├─ fchmod 0444（POSIX）
                 ├─ stage.parent.replace_to(...)  → os.replace(src_dir_fd, dst_dir_fd)
                 └─ 发布后再次核验 inode 身份 + fsync 目录
```

### 3.2 remove

```
cmd_remove → audit_writer（先打开并核验审计文件）
  → remove_file
       ├─ validate_relative_path
       ├─ storage_root 设备 != 系统盘
       ├─ directory(storage_root) / directory(target.parent)
       ├─ stat 目标：regular + 同设备 + nlink=1
       ├─ root.verify_visible()
       ├─ before_unlink = verify_audit（审计身份未变才允许删除）
       └─ unlink(dir_fd) → 目录身份核验 + fsync
```

## 4. 安全属性与测试映射

| 属性 | 实现 | 测试 |
|---|---|---|
| 护栏默认开启 | `config.py` 默认 `True` | `test_guard_defaults_true_when_key_absent` |
| 仅显式布尔 false 可关闭 | `isinstance(bool)` 严格校验 | `test_guard_rejects_non_boolean_values` |
| 设备不可确认时 fail closed | `SystemDiskGuardError` | `test_sync_device_probe_error_fails_closed` |
| 写入前完成检查 | `check_system_disk` 早于 staging | `test_sync_refuses_before_writes_when_space_check_fails` |
| 符号链接不可穿越 | `O_NOFOLLOW` + 每级核验 | `test_mount_race.py` / `test_paths.py` |
| 挂载点切换竞态 | 父目录/文件 inode 身份复核 | `test_publish_rechecks_guard_before_replace` 等 |
| 发布不覆盖非法目标 | regular/nlink/device 检查 | `test_replace_errors_keep_old_bytes_and_clean_stage` |
| 审计与删除联动 | 审计 fd 身份核验 | `test_remove_rejects_audit_descriptor_on_unexpected_device` |
| Windows 盘符根目录 | `probe != probe.parent` | `test_capacity_probe_stops_at_drive_root` 等 4 项 |
| T1–T5 不回退 | `range_transport.py` 与 dev 同源 | `test_peak_download.py`（26 项） |

## 5. 测试基线对比

| 基线 | 结果 |
|---|---|
| `dev`（隔离 worktree，2026-11-26 实测） | **157 passed / 1 skipped** |
| `feature/v0.6-alpha` 移植后 | **195 passed / 1 skipped** |

差值 +38 项全部来自 alpha 侧独有的测量/审计测试：

| 新增测试 | 数量 |
|---|---:|
| `test_peak_probe.py` | 7 |
| `test_collect_box_probe.py` | 4 |
| `test_cmin_experiment.py` | 3 |
| `test_metrics_persistence.py` | 7 |
| `test_bench_harness.py` | 13 |
| `test_windows_paths.py` | 4 |
| 合计 | **38** |

跳过项：`tests/test_paths.py:71`（Windows 创建符号链接需要特权）——不统计为通过。

## 6. 未移植内容与理由

- `62b0cb8` 的运维文档（sshd 事件、性能基线）：与安全代码路径无关，避免把
  dev 的文档演进整包带入 alpha。
- `75e65b1` 合并提交：无独立内容。
- dev 的 `V05_CODE_REVIEW.md` / `RELEASE_NOTES` 等：alpha 已有等价文档。

结论：安全实现与测试覆盖已恢复；没有为凑测试数编写空洞测试；没有整分支合并。
