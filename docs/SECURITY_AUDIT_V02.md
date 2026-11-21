# Pool V0.2 公网安全审计

> 审计时间：2026-11-21 · 对象：dl.bszx.site 公网入口 + Pool 全栈
> 方法：外部真实客户端（大陆移动宽带）+ 服务器本地核查 + 代码复查

## 1. 逐项审计结果

| # | 项目 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | SSRF / 任意 URL 反代 | ✅ 通过 | CLI 只接受 asset_id；URL 白名单 raw.githubusercontent.com + objects.githubusercontent.com + 127.0.0.1（回环测试资产）；公网面无任何代理功能 |
| 2 | Path Traversal | ✅ 通过 | 外部实测 `../`、`%2e%2e`、混合编码全部 400/404；validator 三层校验（字符白名单 + normpath 恒等 + realpath 落根）；symlink 逃逸测试在服务器通过 |
| 3 | 任意文件读取 | ✅ 通过 | 仅 /srv/pool/data 内文件可访问；404 兜底不泄露存在性 |
| 4 | 任意文件写入 | ✅ 通过 | 外部 POST/PUT/DELETE 实测 403（error log 留痕：client 117.182.13.133）；nginx limit_except |
| 5 | 未授权图片访问 | ✅ 通过 | pending（owner-picks/blue-maid.png）与 denied（blue-fish-originals/…）公网均 404 —— 从未同步，公网无文件；CLI 层 pending/denied sync 拒绝（测试覆盖） |
| 6 | 任意 HTTP 方法 | ✅ 通过 | limit_except GET HEAD；POST/PUT/DELETE → 403 |
| 7 | 不受信任重定向 | ✅ 通过 | 下载器逐跳域名白名单校验；测试覆盖跳板拒绝场景 |
| 8 | TLS 配置 | ✅ 通过 | LE 正式证书、链验证 ok、TLS 1.3、无自签/无过期；下载端强制证书校验 |
| 9 | 日志泄露 | ✅ 通过 | 日志在 /var/log 与 /srv/pool/var（非公开根）；`.log/.jsonl/.yaml` 与隐藏文件公网 404 |
| 10 | 流量滥用监控 | ✅ 就绪 | traffic-stats（今日/本月/状态码计数/70-85-95% 阈值）；日志反映 Pool vhost 流量，非运营商结算口径 |
| 11 | 目录索引 | ✅ 通过 | 目录 404（无泄露）；根路径 404 |
| 12 | Nginx 版本泄露 | ✅ 通过 | `Server: nginx`（server_tokens off，:80 与 :443 均生效；stock default 站未动，属 nginx 包自带）|
| 13 | 服务账号权限 | ✅ 通过 | pool 用户 nologin/无 sudo；www-data 只读 data/；文件 0444；配置 0640 root:pool |
| 14 | 数据盘权限/护栏 | ✅ 通过 | vdb1 挂 /srv/pool（fstab nofail）；`guard_system_disk` 在掉挂载时拒绝 sync（78 项测试含此场景）；磁盘使用率分级预警 |

## 2. V0.1 测试保留确认

V0.1 的全部 77 项服务器测试 + 本地 76 项继续运行且通过（V0.2 新增护栏测试后
服务器 **78 项**）。未删除、未放松任何旧断言；唯一改动是把公网目录检查的
预期从"403"放宽为"403 或 404"（404 更严格，不泄露存在性）。

## 3. 残余风险（如实列出）

| 风险 | 等级 | 说明 |
| --- | --- | --- |
| root 密码 SSH 公网开放 | **高** | 历史现状；建议尽快改密钥登录 + 强密码/改端口/fail2ban |
| 服务器无防火墙 | 中 | 所有端口公网可达；Pool 仅新增 80/443 监听；建议按需收紧 |
| nginx stock default 站 :80 | 低 | 公网可见 nginx 欢迎页（非 Pool 引入）；可 `rm /etc/nginx/sites-enabled/default` 关闭 |
| 临时域名依赖 | 低 | dl.bszx.site 为测试域名，正式上线换域名即可（存储与授权解耦已就绪）|
| expected_sha256 人工登记 | 低 | 白名单哈希正确性依赖站长核对 GitHub 文件 |
| HSTS 未启用 | 低 | 按任务书暂缓，待域名稳定后评估 |

## 4. 结论

公网入口安全边界完整：只读、只 GET/HEAD、只服务白名单同步过的文件、
授权链路（pending/denied 拒绝）经外部实测有效。残余风险均为服务器层面
历史现状，与 Pool 引入的攻击面无关，但建议尽快处置 SSH 项。
