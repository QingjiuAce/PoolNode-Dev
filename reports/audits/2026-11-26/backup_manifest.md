# 备份清单（阶段 0）

> 建立时间：2026-11-26 03:0x +0800
> 位置：`F:\鲸鲸\GitHub\开源二创\_pool-backup-2026-11-26\`（**仓库目录之外**，与 Pool 同级）
> 性质：**仅本地备份**。未执行任何远程推送、未上传异机。

## 1. 备份内容

| 子目录/文件 | 文件数 | 大小 | 内容 |
|---|---:|---:|---|
| `git/pool-all-20261126.bundle` | 1 | 326 KB | `git bundle --all`：11 个本地分支 + 5 个远端跟踪分支 + 1 个 tag + HEAD，共 18 refs |
| `worktree/unstaged.diff` | 1 | — | 全部未暂存改动（11 文件） |
| `worktree/staged.diff` | 1 | 0 B | 暂存区（审计时为空） |
| `worktree/status.txt` | 1 | — | porcelain 状态 |
| `worktree/modified-files/` | 11 | — | 11 个改动文件的原始副本（保留目录结构） |
| `data/benchmarks/` | 46 | 653 KB | `reports/benchmarks/` 全量副本（含 RC、C_MIN、2026-11-25 归档、v06 监控） |
| `docs/` | 7 | 37 KB | `refs.txt`、`graph.txt`、`log_all.txt`、`reflog.txt`、`stash.txt`、`README.md`、`requirements.txt` |
| `SHA256SUMS` | 1 | 8 KB | 上述全部 70 个文件的 SHA-256（`sha256sum` 格式） |

全仓 `.git` 体积约 950 KB（212 个松散对象 + 1 个 pack），Bundle 完整记录历史。

## 2. 验证记录

### 2.1 Bundle 完整性

```
$ git bundle verify .../pool-all-20261126.bundle
... is okay
The bundle contains these 18 refs: ...
The bundle records a complete history.
```

`git bundle list-heads` 显示关键 refs：

| ref | 对象 |
|---|---|
| `refs/heads/feature/v0.6-alpha` | `1e38122a6e1674567d5e54fea1c5310fc6c0903b` |
| `refs/heads/dev` | `75e65b121550cc2b7e4a53eee0bd8245cf4b3cc7` |
| `refs/heads/release/v0.5` | `03764440b22eec7c8be59974c602228cfc74b477` |
| `refs/heads/investigate/v05-chunk-queue` | `98a4e0a3cd91b961871baef46109827420639d30` |
| `refs/tags/v0.5.0-rc.1` | `24317ae889b32c688b077fcbfa29a4f7123acc6e` |

### 2.2 工作区可还原性（实测，不是推断）

在仓库外临时 worktree（detached @ `1e38122`）上：

1. `git apply --check worktree/unstaged.diff` → **通过**；
2. 实际 `git apply` → 恢复 11 个文件修改；
3. `diff -q` 对比恢复后的 `v05-rc1/metrics/1.jsonl` 与事故现场文件 → **逐字节一致**；
4. 验证后 worktree 已移除，未触碰主工作区。

### 2.3 关键文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `git/pool-all-20261126.bundle` | `7fd332b1c9fd162b1c4451c448d84690bc653ca54160b9a67dc07ffab8d365c7` |
| `worktree/unstaged.diff` | `6f405fc9bed462cd5bbbf965b1066979cf1bc3e783cf82e8825d684931cf5874` |
| `data/benchmarks/v05-rc1/metrics/1.jsonl`（劣化窗副本） | `b1c906b5d9569abe562cbf6a0f8632207e603c4204cb00b1f4636a578341c4bd` |
| `data/benchmarks/cmin-experiment/runs.jsonl` | `858d6bf442c2f7454bb3194ad2c02ea59fface75a631d87273490dd7c17fba6c` |

完整 70 项清单见 `backup_sha256.txt`（与备份目录内 `SHA256SUMS` 同内容）。

## 3. 恢复方法

```bash
# 1) 从 Bundle 恢复全部分支与 tag 到一个新克隆
git clone -b feature/v0.6-alpha <backup>/git/pool-all-20261126.bundle restored-pool
# 2) 在新克隆中恢复当时未提交的工作区改动
git apply <backup>/worktree/unstaged.diff
# 3) 校验
sha256sum -c <backup>/SHA256SUMS   # 在备份目录内执行
```

注意：Bundle 只含已提交对象，**不含**未提交工作区改动；改动由 `unstaged.diff` 与 `modified-files/` 双份保全。

## 4. 边界声明

- 本备份**不是异机备份**，也**不是远程备份**；与源仓库同处一块物理磁盘（`F:`）。磁盘故障会同时损坏两者。
- 未执行 `git push`，未在 GitHub 创建/移动任何 Tag。
- 阶段 4 将在整改提交完成后建立第二份 Bundle，并再次校验。
- 原始实验数据在备份时点未被修改；阶段 1 的恢复操作只新增目录，不改写这两份来源。
