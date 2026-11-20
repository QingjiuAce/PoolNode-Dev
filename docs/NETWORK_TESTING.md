# 大陆网络测试指引（V0.2）

> 测试方向：**中国大陆客户端 → 香港 Pool（dl.bszx.site）**
> 不能用香港→GitHub Raw 的回源数据代替本方向。

## 1. 工具

| 平台 | 脚本 |
| --- | --- |
| Windows（主力，PowerShell 5.1+，无需 WSL）| `scripts/china-speedtest.ps1` |
| Linux / macOS / Git Bash | `scripts/china-speedtest.sh` |

## 2. 使用方法（Windows）

```powershell
# 关闭系统代理/VPN 后执行（脚本检测到代理会拒绝，保证直连真实路由）
powershell -ExecutionPolicy Bypass -File scripts/china-speedtest.ps1 `
    -Url https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg `
    -Isp mobile -Rounds 3
```

- `-Isp`：`unicom / telecom / mobile / broadcast / other`（**手动选择**，不按 IP 猜测）
- `-Rounds`：每轮测试的下载次数（默认 3）
- `-SkipWinInetCheck`：仅当系统挂着代理但已确认 curl.exe 不走系统代理时使用；
  代理存在的事实会如实写入报告（`wininet_proxy_present=true`）。

## 3. 输出

```
reports/network-tests/<日期>/
    china-unicom.json
    china-telecom.json
    china-mobile.json
    china-broadcast.json
    summary.md            ← 四运营商汇总，未测项标 NOT TESTED
```

每轮记录：时间戳、运营商（手动选择）、DNS 解析 IP、TCP/TLS 握手、TTFB、
HTTP 状态、下载字节、速度（B/s → Mbps）、总耗时、SHA256、失败/中断详情。
响应头与代理检查结果一并入档。

## 4. 测试矩阵要求

| 运营商 | 空闲时段 | 晚高峰 20:00–23:00 |
| --- | --- | --- |
| 中国联通 | 待测 | 待测 |
| 中国电信 | 待测 | 待测 |
| 中国移动 | ✅ 2026-11-21 07:22 样本 | 待测 |
| 中国广电 | 条件允许时 | 条件允许时 |

## 5. 诚实声明

- 本报告只包含实际执行过的测量；未执行的运营商一律 NOT TESTED，不填充推测值；
- 单线程 1.7MB 小文件下载受 TCP 慢启动 + TLS 握手影响，**单流速度不代表端口能力**；
- 服务商官方带宽（300 Mbps）是端口上限，不等于任何用户实际体验；
- 服务器所在 AS137899 到三大运营商的回程路由（V0.1 实测：电信 CN2+163、
  移动 CMI、联通 163→219.158，均直连）仅供背景参考。
