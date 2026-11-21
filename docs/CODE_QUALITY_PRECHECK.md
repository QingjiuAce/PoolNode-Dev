# Code Quality V0.1 — Phase 0 基线

## Git 与工作区

开始时在 `master`，实际 HEAD 为 `63c0ef8376fe40807c1b981388640bbb28d5e6ec`。`git log -5 --oneline` 仅有两条：`63c0ef8`（V0.2 公网部署）和 `b6f556e`（V0.1 内部 PoC）。无已暂存或已跟踪的未提交修改；`git worktree list` 仅列出当前工作区，维护分支原先不存在。从实测 HEAD 创建 `maintenance/code-quality-v01`。没有重置、清理或回退主分支。

开始时已有三份未跟踪文档，均不纳入本次变更：

| 文件 | 基线 SHA256 |
| --- | --- |
| `docs/OPTIMIZATION_ROADMAP.md` | `3088B8CC02B7CAB178168D9D7FA23A838D7A01342182D66FC1F23E47E8284598` |
| `docs/PERFORMANCE_AUDIT.md` | `4F845D79EFAC1A103A234A415A3C18F7273649771704E216AD7C2070997D4BB6` |
| `docs/PERFORMANCE_BASELINE.md` | `88609B1C64F5349606CC7BA6750850DF0FFD55DF0EF2BF06CCFBDA4187FF3D77` |

## 修改前实测

| 检查 | 结果 |
| --- | --- |
| 默认沙箱内 `pytest` | 收集 78；41 通过、0 断言失败、0 跳过、37 个准备阶段错误（`tmp_path` 目录访问被拒）。尝试独立临时目录仍受限。 |
| 沙箱外原命令 `pytest`（Windows/Python 3.14.5/pytest 9.2.0） | 78 项：76 通过、0 失败、2 跳过、1 条外部 anyio 插件弃用警告。Windows 跳过系统盘设备测试和符号链接测试。未更改 Windows 权限。 |
| `git diff --check` / `git diff --cached --check` | 均通过。 |
| Git Bash `bash -n` | 七个 Bash 脚本全部通过；沙箱内 Git Bash 启动信号管道受限，语法检查在沙箱外完成。 |
| PowerShell AST 解析 | Windows PowerShell 5.1 和 PowerShell 7.7.0 均通过；未执行测速。 |
| Nginx / ShellCheck | 未发现可直接调用的本地程序；模板静态阅读。 |

## 审查范围与维护切入点

- 通过 `git ls-files` 盘点 `pool/`、`config/`、`scripts/`、`tests/`、`docs/`、`reports/` 和根目录文件。审阅 README、架构、安全和部署文档、全部核心 Python 模块、四个测试模块和两个测试支持文件、七个 Bash 脚本、PowerShell 测速脚本及三个 Nginx 模板。
- CLI 串联配置与白名单、授权、下载、校验、存储和审计。同步函数兼有磁盘探测、工作流和显示；适合提取少量私有辅助函数，不新增架构层。URL 入库校验和重定向检查的职责不同；诊断取样的图片特征和发布格式判定也不等价，不强行合并。
- 现有 pytest 覆盖授权分支、上游限制、发布替换、哈希、路径、删除。尚缺明确的流式超限清理、本地哈希和 CLI 入口契约等特征测试。回环冒烟与公网验收是独立工具；本阶段未执行，不把历史公网验收当成本次结果。无现有覆盖率或 formatter/linter 约定。
- Bash/PowerShell 测速的字段类型和计时口径不完全相同；现有 Bash 报告组装及部署脚本还有待独立修复的行为。维护阶段保持报告 Schema、计时、退出码及部署行为，不借可读性修改修复功能缺陷。
