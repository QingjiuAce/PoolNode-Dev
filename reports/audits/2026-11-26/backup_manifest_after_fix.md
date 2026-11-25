# 整改后备份清单（阶段 3 完成后）

> 建立时间：2026-11-26 03:5x +0800
> 位置：`F:\鲸鲸\GitHub\开源二创\_pool-backup-2026-11-26\git\pool-all-20261126-after-fix.bundle`
> 性质：**本地备份**。未推送远程，未做异机备份。

## 1. Bundle 信息

| 项 | 值 |
|---|---|
| 文件 | `pool-all-20261126-after-fix.bundle` |
| 大小 | 424,512 B |
| SHA-256 | `b707026283511ca65d48506946537d1ccd680ba913eaf58b268dc493d114b631` |
| refs | 18（11 本地分支 + 5 远端跟踪 + 1 tag + HEAD） |
| `feature/v0.6-alpha` | `0b53e12174e0dcfbebf32f50659f501f620b9f23` |
| `release/v0.5` | `03764440b22eec7c8be59974c602228cfc74b477`（未变） |
| `v0.5.0-rc.1` | `24317ae889b32c688b077fcbfa29a4f7123acc6e` → `0376444`（未移动） |

## 2. 验证记录

| 验证 | 结果 |
|---|---|
| `git bundle verify` | **is okay**，18 refs，完整历史 |
| 从 Bundle clone `feature/v0.6-alpha` | HEAD = `0b53e12` ✅ |
| 新克隆存在 `pool/_guarded_io.py` | ✅ |
| `pool/storage.py` blob 与工作区一致 | ✅ |
| `ERRATA_2026-11-26.md` blob 一致 | `ca352b93…` ✅（文本差异仅为 CRLF 检出） |
| 新克隆含 tag `v0.5.0-rc.1` | ✅ |
| 工作区状态 | **干净**（0 修改 / 0 暂存 / 0 未跟踪） |

## 3. 本次纳入的整改提交

| 提交 | 内容 |
|---|---|
| `233b96f` | 阶段 0 基线 + 阶段 1 metrics 双窗恢复与防覆盖 |
| `1cb0d74` | 阶段 2 RC Harness 策略修复 + 勘误 + 复测计划 |
| `7d08faa` | 2026-11-26 01:15 C_MIN 数据（40 臂，旧配对顺序） |
| `0b53e12` | 阶段 3 Alpha 安全基线恢复（guarded IO / 系统盘护栏 / Windows 根目录） |

`1e38122`（阶段 0 前 HEAD）与 `0b53e12` 之间的全部提交均在 Bundle 内。

## 4. 与阶段 0 备份的关系

- 阶段 0 Bundle（`pool-all-20261126.bundle`，`7fd332b1…`）是事故现场快照，
  **必须保留**：它记录了整改前的工作区 diff 来源与原始 refs。
- 本 Bundle 是整改后检查点，覆盖阶段 0–3 的提交。
- 两份 Bundle 共用同一外部目录；`SHA256SUMS` 只覆盖阶段 0 备份，
  本文件的 SHA 单独记录，互不覆盖。

## 5. 恢复方法

```bash
git clone -b feature/v0.6-alpha \
  <backup>/git/pool-all-20261126-after-fix.bundle restored-pool
cd restored-pool
git log --oneline -4       # 应看到 0b53e12 .. 233b96f
python -m pytest tests/ -q # 应得到 195 passed / 1 skipped
```

## 6. 边界声明

- **本地备份**：与源仓库同盘（`F:`），不是异机/远程备份。
- 未执行 `git push`、未创建远程 Tag、未创建 GitHub Release。
- 阶段 5–7 若产生新提交，将在最终收口时追加 Bundle 或明确说明本检查点的覆盖范围。
