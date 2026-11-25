# Pool 源码导览（Code Quality V0.1）

从 `python -m pool` 进入 [__main__.py](../pool/__main__.py)，再进入 [cli.main](../pool/cli.py)。本导览面向仓库源码；历史部署实测见 [V0.2 验收](V02_TEST_REPORT.md)，不是本次重新验收。

| 模块 | 职责与入口 |
| --- | --- |
| [CLI](../pool/cli.py) | `build_parser`、`main` 加载配置、选择命令并映射退出码。`cmd_sync` 编排授权→存储护栏→下载→校验→发布→审计；其他命令独立编排。 |
| [config](../pool/config.py) | `load_config` 将 YAML 转为 `PoolConfig` / `UpstreamConfig`；文件大小和盘余量在运行对象内以字节为单位。配置解析不下载、不创建存储目录。 |
| [permissions](../pool/permissions.py) | `load_assets` 将清单转为 `Asset` 列表，`get_asset` 只按 ID 查找，`check_permission` 判定同步授权，`validate_source_url` 验证初始 URL 与登记路径对应。 |
| [downloader](../pool/downloader.py) | `download_to_file` 流式写入调用方提供的暂存路径，独立检查传输 URL/重定向和字节上限；`check_upstream` 分阶段诊断，不跟随 HTTP 重定向、不落盘。两者都使用系统 CA 验证 HTTPS。 |
| [validator](../pool/validator.py) | `validate_relative_path` 先查词法规范，`resolve_safe` 再查物理路径范围；`check_format` 通过文件头检查，不信任扩展名或 HTTP Content-Type；`sha256_file` 流式计算哈希。 |
| [storage](../pool/storage.py) | `new_tmp_file` 创建隔离暂存文件，`publish` 复查落点后用 `os.replace` 发布，`remove_file` 删除解析后的目标，`record` / `last_record_for` 写读 JSONL。此模块不判定版权许可。 |

## 顺着数据读代码

1. `main` 从 `--config` 加载配置，清单默认从配置文件同目录加载，可用 `POOL_POC_ASSETS` 覆盖。命令只接收资产 ID，不接收 URL 或删除路径。发布授权需要合法清单中的 `approved` 和非空证据；`pending`、`denied`、缺证据分别拒绝。`check-upstream` 不是同步，不执行发布授权关。
2. `sync` 先复核清单 URL、目标路径和磁盘，再写暂存。下载器先查响应声明大小，再累计实际字节；CLI 最后检查返回大小、内容格式和哈希。登记 `expected_sha256` 时必须匹配才叫 `official`；未登记时仅记录 `local`，不证明来源真实性。
3. 存储层在发布前重新解析目标。同文件系统直接替换；跨文件系统在目标目录以隐藏 staging 名复制并同步，再替换最终文件名。校验失败不会发布本次内容；已有公开文件不因此撤销。发布和审计写入不是同一事务。
4. `verify` 对存在的目标计算哈希并检查格式；与官方登记和最近一条已发布的同步记录（如果最近一条同步记录为 published）分别比较。`remove` 根据已登记路径解析删除，不需要再次授予同步许可。
5. [回环模板](../scripts/nginx-pool.conf.template)和[公网 HTTPS 模板](../scripts/nginx-pool-public-https.conf.template)直接读公开目录，不调 Python，也不逐请求读取清单；暂存、审计和配置不是静态资源。GET、HEAD、Range 由 Nginx 静态模块处理。公网 [HTTP 模板](../scripts/nginx-pool-public-http.conf.template)保留独立 ACME webroot，并将其他请求导向 HTTPS。

## 测试和运维入口

[pytest](../tests) 使用合成数据、替身和回环 HTTP。部署、冒烟、统计和测速从 [scripts](../scripts) 进入；它们与 CLI 运行时分离。不要用部署命令验证源码重构，也不要把脚本解析通过当作公网 HTTP 实测。
