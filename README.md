# Pool PoC V0.1 — 独立备用原图下载节点

面向 [蓝色大肥鱼.com](https://蓝色大肥鱼.com) 的独立、只读、可撤销的备用原图下载服务。
与现有 GitHub Raw 发布体系完全解耦：不替换 Raw、不改原站代码、不改已发布 URL。

**许可说明**：本仓库的 MIT 许可仅覆盖 Pool 自身代码与文档。
任何图片文件的授权以 `/etc/pool-poc/assets.yaml` 中各资产的
`attribution / license / permission_evidence` 字段为准 —— 图片著作权始终归原作者，
未经站长明确批准（approved + 留证）的资产不会被同步或发布。

## 架构一览

```
pool-poc CLI（授权→下载→SHA256→原子发布）
        ↓
/srv/pool/data/<repo相对路径>    ← 50G 数据盘，Nginx 只读静态服务
        ↓
https://<POOL_DOMAIN>/<repo相对路径>
```

详见 [架构设计](docs/ARCHITECTURE.md)；从源码接手请看 [源码导览](docs/CODE_ARCHITECTURE_MAP.md) 和 [维护风格](docs/CODE_STYLE_GUIDE.md)。

## 快速开始（本地开发）

```bash
pip install -r requirements.txt pytest
python -m pytest tests/
```

## 快速开始（服务器部署）

```bash
# 1. 上传项目到服务器 /opt/pool-poc/src（scp/sftp 均可）
# 2. 安装系统依赖与目录（含挂载 50G 数据盘）
sudo bash scripts/install.sh --mount
# 3. 部署代码与配置（默认回环模式，域名就绪前不公网暴露）
sudo bash scripts/deploy.sh
# 4. 同步测试资产并验证
python3 -m pool --config /etc/pool-poc/config.yaml sync test-poc-001
bash scripts/smoke-test.sh
```

完整手册：[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · 回滚见其末节。

## CLI

| 命令 | 作用 |
| --- | --- |
| `pool-poc check-upstream <asset_id>` | 上游连通性诊断（DNS/TCP/TLS/HTTP 分阶段，不落盘）|
| `pool-poc sync <asset_id>` | 授权检查→流式下载→格式/大小/SHA256 校验→原子发布 |
| `pool-poc verify <asset_id>` | 校验已发布文件与官方哈希/同步记录的一致性 |
| `pool-poc remove <asset_id>` | 删除白名单资产对应的物理文件（留痕）|

## URL 结构

URL = 图片在 [lmy414/ai-girl-stickers](https://github.com/lmy414/ai-girl-stickers)
仓库中的相对路径，与 GitHub Raw 一比一对应：

```
https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/blue-maid.png
                                                              ↓ 换域名即可
https://<POOL_DOMAIN>/owner-picks/blue-maid.png
```

收录的路径：`dist/submissions/originals/<角色>/<文件名>`、`owner-picks/<文件名>`、
`blue-fish-originals/<文件名>`（以白名单实际登记为准）。

## 目录

```
config/    配置示例（config / assets 白名单）
pool/      Python 源码（config permissions validator downloader storage cli）
scripts/   install / deploy / smoke-test / traffic-stats / remove-asset
           / logrotate / china-speedtest（大陆测速脚本）
tests/     pytest 自动化测试
docs/      ARCHITECTURE / DEPLOYMENT / SECURITY / CHINA-TEST / TEST_REPORT
```

## 状态

- [x] V0.1 内部 PoC：核心实现 + 78 项测试 + 回环静态服务（docs/TEST_REPORT.md）
- [x] V0.2 公网 PoC：**https://dl.bszx.site** HTTPS 上线（临时测试域名）
  - DNS/证书/301 跳转/自动续期全部验证（docs/TLS_DEPLOYMENT.md）
  - 公网验收 33/33：GET/HEAD/Range/SHA256 三方闭环/安全边界（docs/V02_TEST_REPORT.md）
  - 大陆测速工具就绪：`scripts/china-speedtest.ps1`（Windows）/ `.sh`（Linux/macOS）
  - 未测项：联通/电信/广电样本、晚高峰（20:00–23:00）样本 → **NOT TESTED**
- [ ] 真实图片托管：**等待站长明确授权**（公网目前仅自生成测试图片）
- [ ] 正式域名：临时域名切换成本已最小化（域名不入核心逻辑）
