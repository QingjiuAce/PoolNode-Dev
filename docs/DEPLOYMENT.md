# Pool PoC V0.1 部署手册

> 目标服务器：156.233.227.20（香港 · Debian 12 · 50G 数据盘 /dev/vdb1）
> 代码上传方式：SFTP（本仓库 scripts 无任何硬编码凭据，密码通过 SSH 客户端交互输入）

## 1. 前置条件

| 条件 | 状态 |
| --- | --- |
| SSH 可达（22 端口）| ✅ |
| /dev/vdb1 为空 ext4（debugfs 只读核实）| ✅ |
| 服务器无其他 web 服务 / 无其他 vhost | ✅ |
| 域名与 DNS | ⏳ 用户后续提供；就绪前仅回环模式 |

## 2. 部署步骤

### 2.1 上传代码

把整个项目上传到服务器 `/opt/pool-poc/src/`（保持目录结构，`config/config.yaml`
与 `config/assets.yaml` 若有生产版本一并放入 `config/`，没有则使用 example）。

### 2.2 安装系统依赖与目录

```bash
cd /opt/pool-poc/src/scripts
sudo bash install.sh --mount     # --mount 挂载 50G 数据盘到 /srv/pool/data
```

脚本行为：安装 nginx + python3-yaml + logrotate；创建系统用户 `pool`；
挂载数据盘（先校验 vdb1 是 ext4，fstab 先备份，`nofail` 选项）；建目录与权限。
不改防火墙、不动 SSH、不重启机器。

### 2.3 部署代码与配置（回环模式）

```bash
sudo bash /opt/pool-poc/src/scripts/deploy.sh
```

- 代码 → `/opt/pool-poc/pool`（部署前自动 tar 备份旧版）
- 配置 → `/etc/pool-poc/{config,assets}.yaml`（root:pool 0640）
- Nginx vhost → `/etc/nginx/sites-available/pool.conf`，监听 `127.0.0.1:8080`
- `nginx -t` 失败会自动回滚 vhost 软链

### 2.4 同步测试资产 + 冒烟测试

```bash
# 1. 生成确定性测试 PNG（打印 sha256，与 assets.yaml 白名单一致）
mkdir -p /tmp/pool-test-src/pool-tests
python3 /opt/pool-poc/src/scripts/gen-test-image.py \
        /tmp/pool-test-src/pool-tests/test-poc-001.png 64 64 1

# 2. 启动临时回环图片源（仅 127.0.0.1:8000，给 sync 提供上游）
cd /tmp/pool-test-src && nohup python3 -m http.server 8000 --bind 127.0.0.1 >/dev/null 2>&1 &

# 3. 以 pool 用户身份 sync（assets.yaml 中 test-poc-001 指向回环 URL）
runuser -u pool -- bash -c 'export PYTHONPATH=/opt/pool-poc; \
    python3 -m pool --config /etc/pool-poc/config.yaml sync test-poc-001'

# 4. 冒烟测试（GET/HEAD/Range/404/穿越/方法限制/头检查，23 项）
bash /opt/pool-poc/src/scripts/smoke-test.sh

# 5. 完整性验证
runuser -u pool -- bash -c 'export PYTHONPATH=/opt/pool-poc; \
    python3 -m pool --config /etc/pool-poc/config.yaml verify test-poc-001'

# 6. 测完关闭回环图片源
pkill -f "http[.]server 8000"
```

### 2.5 切换公网模式（域名就绪后）

```bash
# 1. DNS A 记录把 <POOL_DOMAIN> 指到 156.233.227.20 并生效
# 2. 签发证书（webroot 模式，不中断服务）
sudo certbot --nginx -d <POOL_DOMAIN>   # 或 certbot certonly --webroot -w /srv/pool/data
# 3. 部署公网 vhost
sudo POOL_MODE=public POOL_DOMAIN=<POOL_DOMAIN> bash /opt/pool-poc/src/scripts/deploy.sh
# 4. certbot 会在 vhost 中注入 443 ssl 配置；再次 nginx -t && reload
# 5. 外部验证: curl -fSLO https://<POOL_DOMAIN>/pool-tests/test-poc-001.png
```

> 注：webroot 验证需要 HTTP 80 可访问 ACME 路径，certbot --nginx 会自动处理。

## 3. 例行运维

```bash
# 流量统计（建议 cron 每日 06:00 执行一次，写入累计文件）
bash /opt/pool-poc/src/scripts/traffic-stats.sh 1000

# 查看同步/删除审计记录
cat /var/log/pool-poc/sync-records.jsonl

# 新增资产流程：
#   1) 编辑 /etc/pool-poc/assets.yaml（status=approved + evidence + expected_sha256）
#   2) python3 -m pool sync <asset_id>
#   3) python3 -m pool verify <asset_id>

# 紧急下架单张图
bash /opt/pool-poc/src/scripts/remove-asset.sh <asset_id>
```

## 4. 回滚方案

Pool 是完全独立的新增组件，回滚不影响服务器上任何其他服务、
不影响蓝色大肥鱼原站、不修改 GitHub Raw 资源：

```bash
# 1. 停止 Pool 对外服务（保留数据）
sudo rm /etc/nginx/sites-enabled/pool.conf && sudo nginx -t && sudo systemctl reload nginx

# 2. 卸载数据盘（数据保留在 /dev/vdb1，随时可再挂载）
sudo umount /srv/pool/data
sudo sed -i '\|/srv/pool/data|d' /etc/fstab     # 删除 Pool 追加行（或恢复 fstab.pool-backup.*）

# 3. 彻底移除 Pool 自有文件与用户
sudo rm -rf /opt/pool-poc /srv/pool /etc/pool-poc /var/log/pool-poc \
            /etc/nginx/conf.d/pool-log.conf \
            /etc/logrotate.d/pool-logs 2>/dev/null
sudo userdel pool
# 若 nginx 是为本项目安装且无其他站点使用，可整体卸载：
# sudo apt-get purge nginx && sudo apt-get autoremove
```

代码历史版本备份位于 `/opt/pool-poc/backup/*.tgz`。

## 5. 大陆网络测试

见 [CHINA-TEST.md](CHINA-TEST.md)：在大陆电脑运行 `scripts/china-speedtest.sh`，
分别在空闲时段与北京时间 20:00–23:00 各测一次，覆盖联通/电信/移动/广电。
