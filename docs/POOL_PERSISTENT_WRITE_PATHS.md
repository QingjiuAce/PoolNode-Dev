# V0.3 持久写入路径清单

保护开启时的 CLI 调用链如下。路径从配置读取；`/srv/pool` 是示例，不代表已核实的生产配置。

| 函数 / 调用位置 | 写入路径与操作 | 设备检查与失败行为 | 证据 |
| --- | --- | --- | --- |
| `cmd_sync` → `guarded_staging` → `download_to_file` → 清理 | `tmp_dir/<id>.*.part`；创建父目录、独占建文件、流式写入、unlink | 检查最近父目录设备；POSIX 使用目录 FD；打开后 `fstat` 检查普通文件、设备及链接数，验证前不下载。退出时按身份清理。 | `test_staging_object_verification_failure_stops_download_and_cleans_file`、`test_sync_refuses_publication_if_device_changes_after_download` |
| `cmd_sync` → `publish` → `_publish_checked` | `storage_root/<relative_path>`；mkdir、权限、原子替换、目录 fsync | 暂存、存储根、目标父目录须同一非系统设备；持有目录 FD，不走复制。失败报告 errno；若替换已执行但后置核验失败，明确告警，不宣称回滚。 | `test_guarded_cross_device_publication_preserves_existing_target`、`test_replace_errors_keep_old_bytes_and_clean_stage` |
| `cmd_remove` → `audit_writer` → `remove_file` | 已登记公开文件；unlink、目录 fsync | 先打开并核验审计文件，再检查目标目录和文件；删除前复核审计身份；审计失败不报完整成功。 | `test_audit_open_denial_prevents_mutation`、`test_remove_reports_post_delete_audit_failure` |
| `cmd_sync` / `cmd_remove` → `record` / `audit_writer` | `records_file`；创建其父目录、追加、flush/fsync | 父目录锚定，检查已打开的普通文件及身份；拒绝链接、设备不匹配；不创建 `tmp_dir` 或备用日志。低余量时只尝试合规审计。 | `test_low_space_keeps_original_rejection_if_audit_fails`、`test_sync_rejects_when_audit_dir_on_system_device` |
| `cmd_verify`、`cmd_check_upstream` | 无显式持久写入；前者读文件和记录，后者不落盘取样 | 不新增审计事件，故没有审计回退路径。 | `test_read_only_commands_do_not_open_audit` |
| 显式 `false` | `new_tmp_file`、`_publish_legacy`、`_append_record`、legacy `remove_file` | 旧路径兼容，不享受系统盘防护；跨盘副本使用独占创建并尽力清理。 | `test_opt_out_keeps_cross_device_copy`、既有同步与删除测试 |

## 运行时之外

- [安装脚本](<../scripts/install.sh>)以管理员权限安装包、创建服务用户与目录，可写入 fstab 与备份，并创建 `/srv/pool/{data,tmp,var}`。不经过应用护栏。
- [部署脚本](<../scripts/deploy.sh>)写代码备份、运行配置、Nginx 站点及软链、日志规则和 ACME 目录。应用护栏不会阻止部署脚本覆盖配置。
- Nginx 访问/错误日志及 [logrotate](<../scripts/logrotate-pool.conf>)、[流量统计](<../scripts/traffic-stats.sh>)位于专用 `/var/log` 路径，是有意独立的运维写入。
- [冒烟](<../scripts/smoke-test.sh>)和[公网验收](<../scripts/public-acceptance.sh>)有各自的临时文件。[测速 Bash](<../scripts/china-speedtest.sh>)、[测速 PowerShell](<../scripts/china-speedtest.ps1>)写报告；[PNG 生成器](<../scripts/gen-test-image.py>)、[JPEG 生成器](<../scripts/gen-test-jpeg.py>)写调用方指定的输出。均不走资产 CLI。
- Python 字节码和 pytest 缓存是解释器/测试工具管理的本地写入，不是 Pool 数据；本轮测试禁用二者。CLI 没有配置生成或业务缓存写入。
