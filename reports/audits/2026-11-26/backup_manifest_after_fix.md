# 整改后备份清单（阶段 4 / 最终检查点）

> 建立时间：2026-11-26 03:5x +0800（最终检查点更新于 2026-11-26 04:0x）
> 位置：`F:\鲸鲸\GitHub\开源二创\_pool-backup-2026-11-26\git\`
> 性质：**本地备份**。未推送远程，未做异机备份。

## 1. 两份整改后 Bundle

| 检查点 | 文件 | 大小 | SHA-256 | alpha HEAD |
|---|---|---:|---|---|
| 阶段 3 后 | `pool-all-20261126-after-fix.bundle` | 424,512 B | `b707026283511ca65d48506946537d1ccd680ba913eaf58b268dc493d114b631` | `0b53e12` |
| 全部整改后 | `pool-all-20261126-final.bundle` | 447,793 B | `82a80523250fa69f434937570eddda740194dcd29755782169672cf274359a88` | `627cb43` |

两份均包含 18 refs（11 本地分支 + 5 远端跟踪 + `v0.5.0-rc.1` + HEAD）。
`release/v0.5` = `0376444`、tag = `24317ae → 0376444`，**未移动**。

## 2. 验证记录

| 验证 | 结果 |
|---|---|
| `git bundle verify`（两份） | **is okay**，完整历史 |
| 从 final bundle clone `feature/v0.6-alpha` | HEAD = `627cb43` ✅ |
| 在新克隆中运行完整测试 | **255 passed / 1 skipped** ✅ |
| 新克隆含 `pool/_guarded_io.py`、`ERRATA_2026-11-26.md` | ✅ |
| 工作区状态 | **干净**（0 修改 / 0 暂存 / 0 未跟踪） |
| 阶段 0 Bundle 保留 | `pool-all-20261126.bundle`（`7fd332b1…`）未动 ✅ |

## 3. 纳入的整改提交（`1e38122..627cb43`）

| 提交 | 内容 |
|---|---|
| `233b96f` | 阶段 0 基线 + 阶段 1 metrics 双窗恢复与防覆盖 |
| `1cb0d74` | 阶段 2 RC Harness 策略修复 + 勘误 + 复测计划 |
| `7d08faa` | 2026-11-26 01:15 C_MIN 数据（40 臂，旧配对顺序） |
| `0b53e12` | 阶段 3 Alpha 安全基线恢复 |
| `bff112a` | 阶段 6 P1：CI、origin 配置、严格 bool、探针/文档/依赖 |
| `627cb43` | 阶段 7 P2：审计追加串行化、fixture 一致性、主机规则测试、仓库卫生 |

## 4. 与阶段 0 备份的关系

- 阶段 0 Bundle 是事故现场快照（含被覆盖 metrics 的工作区 diff），**必须保留**。
- after-fix Bundle 是安全基线恢复后的中间检查点。
- final Bundle 覆盖全部 P0/P1/P2 整改。
- 三者共用同一外部目录；`SHA256SUMS` 只覆盖阶段 0 的 70 个文件，
  整改后 Bundle 的哈希记录在本文件与 `backup_sha256_after_fix.txt`。

## 5. 恢复方法

```bash
git clone -b feature/v0.6-alpha \
  <backup>/git/pool-all-20261126-final.bundle restored-pool
cd restored-pool
python -m pytest tests/ -q   # 255 passed / 1 skipped
```

## 6. 边界声明

- **本地备份**：与源仓库同盘（`F:`），不是异机/远程备份。
- 未执行 `git push`、未创建远程 Tag、未创建 GitHub Release。
- 未部署 V0.5 RC / V0.6 Alpha；服务端 V0.2 与正式分发 v0.4-beta 未改动。
- 远程备份待用户单独授权。
