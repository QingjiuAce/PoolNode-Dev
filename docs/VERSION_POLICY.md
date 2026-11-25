# 版本标识政策

> 单一版本来源：`pool/__init__.py` 的 `__version__`。
> CLI `--version` 与 HTTP User-Agent 均从该常量派生；禁止在其他文件重复维护版本号。
> 本文件描述**发布渠道**与**构建标识**的区分，不参与运行时代码。

## 1. 当前版本层级

| 层级 | 标识 | 载体 | 状态 |
|---|---|---|---|
| 服务端生产 | **V0.2** | `master` @ `63c0ef8` 部署于 `https://dl.bszx.site` | **冻结**，本分支不改动 |
| 正式客户端分发 | **v0.4-beta** | `feature/v0.4-beta` | **当前唯一正式分发版本** |
| RC 候选 | **v0.5.0-rc.1** | 附注 tag → `release/v0.5` @ `0376444` | 仅本地；未发布；策略对比已勘误 |
| 内部开发构建 | **0.6.0.dev1** | `feature/v0.6-alpha` | 内部构建；**不是** Alpha 发布 |

## 2. 为什么包版本与部署版本分离

服务端 V0.2 是**部署标签**，对应某次生产上线的代码与配置快照；包版本是
**代码构建标识**。二者不是同一维度：

- 生产 V0.2 部署自 `master`，该提交的 `__version__` 为 `0.1.0`。
- 客户端从 v0.4-beta 起独立演进，但与服务端共享同一个 Python 包。
- 因此不能把服务端部署标签直接写进 `__version__`，否则会误导客户端构建身份。

对外沟通时请使用“服务端 V0.2 / 客户端 v0.4-beta”的部署标签；排查构建问题时
使用包版本（`python -m pool --version` 或 `pool.fetch` 的 UA/metrics `run_id`）。

## 3. `0.6.0.dev1` 的含义

- 分支目标：v0.6 自适应调度（设计见 `docs/V06_DESIGN.md`）。
- 当前内容：v0.5 动态调度 + T1–T5 + 安全基线 + 测量工具；**自适应算法尚未实现**。
- `dev1` 表示开发快照，不是 Alpha、不是 RC、不是正式发行。
- 任何对外分发、部署或 tag 都需要单独授权；本构建不得被替换为正式分发。

## 4. 单一来源的落地方式

```python
# pool/__init__.py
__version__ = "0.6.0.dev1"

# pool/cli.py
parser.add_argument("--version", action="version",
                    version=f"pool-poc {__version__}")

# pool/downloader.py
def user_agent(purpose: str) -> str:
    return f"pool-poc/{__version__} (+{purpose})"
```

测试 `tests/test_version.py` 验证：

1. `pool.__version__` 为合法 PEP 440 形式；
2. CLI `--version` 输出与包版本一致；
3. User-Agent 与包版本一致；
4. `pool/` 源码中不存在旧的硬编码 `pool-poc/0.1`；
5. 本文件包含当前版本与四个渠道标识。

## 5. 历史版本与 tag

| 标签 | 包版本 | 说明 |
|---|---|---|
| V0.1 / V0.2 | `0.1.0` | 服务端 PoC 与公网上线；无独立客户端 |
| v0.4-beta | `0.1.0` | 客户端首发；包版本当时未同步更新（历史事实） |
| v0.5.0-rc.1 | `0.1.0` | RC 代码；包版本仍未更新（本政策修复的断层） |
| 0.6.0.dev1 | `0.6.0.dev1` | 本分支；首次统一 CLI/UA/包版本 |

历史 tag 与提交**不移动、不重建**；上表只记录事实，不回写历史。

## 6. 发布边界

- 不创建 GitHub Release。
- 不推送 tag。
- 不替换 v0.4-beta 分发。
- 不部署 V0.5 RC / V0.6 Alpha。
- 服务端 V0.2 保持现状。
