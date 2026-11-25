# Pool V0.2 性能基线 · 审计 V0.1

代码基点 `63c0ef8`。日期按香港时间 2026-11-21。只使用公开自生成夹具 `https://dl.bszx.site/submissions/originals/pool-public-smoke-test.jpg`，每份 1,778,514 B，预期 SHA256 `b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e`。Mbps 为十进制正文速率，不含协议开销。

## A. 已归档移动宽带：独立 curl 进程，非新采样

[原始 JSON](../reports/network-tests/2026-11-21/china-mobile.json)，07:26:53，WinINET 代理存在，记录注明 curl 直连；预解析过 DNS，不是递归冷缓存。49.2 ms 为 4 次 ICMP 平均，4/4 收到不代表 TCP 从不丢包。3/3 HTTP 200、大小与 SHA 均匹配。下表为 **ms**，除 TTFB、总时两列外均为阶段而非里程碑。

|轮|DNS|TCP|TLS|TLS 就绪→首字节|TTFB 累计|首字节→结束|总时|全程 Mbps|
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|1|4.829|60.704|63.650|104.171|233.354|285.239|518.593|27.44|
|2|10.322|62.275|64.452|105.247|242.296|286.370|528.666|26.91|
|3|5.381|58.680|60.792|102.582|227.435|309.772|537.207|26.49|

分别对 `time_namelookup, time_connect, time_appconnect, time_starttransfer, time_total` 相邻作差。TTFB 包含 DNS/TCP/TLS，不能再加一次。首字节后速率 45.9–49.9 Mbps，不是全程的 26.9 Mbps。TLS 就绪至首字节含发送请求、往返、排队及读取，不能全算作服务器耗时。

## B. 本次 Windows 测点：已验证 TLS 的 HTTP/1.1 串行 GET

07:40:49 起；运营商、城市、VPN/物理路由未核验，**不能并入 A**。Python 3.14.5 / OpenSSL 3.0.20，直接 IPv4 socket 到解析地址 156.233.227.20:443，保留 SNI、系统信任链和主机名校验，不使用 HTTP 代理。每批一个新 TCP、完整 TLSv1.3（`session_reused=False`），ALPN http/1.1，六次请求共用同一个 SSLSocket。18/18 HTTP 200、哈希和长度正确。响应声明 keep-alive。每对为「开始 GET 至完整响应头 / 读完剩余正文」ms；Python 已解析头，缓冲区可能包含正文，**不是 curl 首字节**。

|批|DNS / TCP / TLS (ms)|第1次|第2次|第3次|第4次|第5次|第6次|整批秒|整批 Mbps|
|---|---|---|---|---|---|---|---|---:|---:|
|1|8.089 / 56.084 / 56.653|150.406/274.182|51.298/54.167|50.478/49.829|50.501/49.383|50.915/48.577|50.552/475.842|1.481821|57.6|
|2|0.521 / 60.936 / 55.977|151.330/300.956|51.194/51.216|50.660/49.396|50.488/49.553|50.885/49.523|50.927/50.237|1.078596|79.1|
|3|0.632 / 57.561 / 57.997|156.902/313.591|53.129/53.285|52.431/49.974|52.579/49.518|52.368/49.495|52.840/49.102|1.106429|77.2|

整批分子 10,671,084 B，分母从解析前到最后一个正文读完，包括中间的哈希、输出及首轮建连。首批第六次尾部延迟保留。后续约 100 ms 的单件可对应约 142 Mbps；这是**单件热请求**，不是含首件的整批速率，也不追认历史 137。复现核心（Python，控制样本量）：

```python
import socket, ssl, http.client, hashlib
from time import perf_counter as now
h='dl.bszx.site'; path='/submissions/originals/pool-public-smoke-test.jpg'
ctx=ssl.create_default_context(); ctx.set_alpn_protocols(['http/1.1'])
for _ in range(3):
    a=now(); addr=socket.getaddrinfo(h,443,socket.AF_INET,socket.SOCK_STREAM)[0][4]; b=now()
    raw=socket.create_connection(addr,7); c=now(); s=ctx.wrap_socket(raw,server_hostname=h); d=now()
    con=http.client.HTTPSConnection(h,timeout=7,context=ctx); con.sock=s
    print(b-a,c-b,d-c,s.version(),s.selected_alpn_protocol(),s.session_reused)
    for i in range(6):
        assert con.sock is s
        t=now(); con.request('GET',path); r=con.getresponse(); u=now(); body=r.read(); v=now()
        assert r.status==200 and len(body)==1778514 and hashlib.sha256(body).hexdigest()=='b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e'
        print(i,u-t,v-u)
    print('batch seconds',v-a); con.close()
```

失败亦记录：Windows curl 8.22 Schannel 未取得本地凭据（exit 35、HTTP 000）；Git curl 不支持尝试的后端选择选项，未发请求。均不算站点 HTTP 故障或计入成功速率；没有关闭证书校验重试。

## C. 本次同测点：协议与验证器

- Python 单独提供 h2/h1，协商 h2；另以 H1 建新连接读响应后取得 TLS 1.3 ticket，用相同 SSLContext 与 `wrap_socket(...,session=旧会话)` 新建 TCP。`session_reused` 从 False 到 True；握手 56.71 / 56.39 ms（各一次）。**恢复成功不等于少一个网络往返**；未用 0-RTT。
- ETag `"6b00d162-1b2352"`、Last-Modified `Fri, 20 Nov 2026 23:01:54 GMT`、`Accept-Ranges: bytes`、`public, max-age=3600, no-transform`。获取 `Range: bytes=0-889256` 得 206/889257 B；新连接 `If-None-Match` 得 304/0 B；`Range: bytes=889257-` 加原值 `If-Range` 得 206/889257 B。两段 Content-Range 起止和总长正确，拼接 SHA 同夹具。只验证未替换对象。
- Node v22.22.2 对同一 URL，ABBA，每批新建一条经验证的 TLS 连接、串行完整 GET 三次、随收随验 SHA；无客户端对象缓存。H1 `https.Agent({keepAlive:true,maxSockets:1,maxCachedSessions:0})`，后两次检查 reusedSocket；H2 `http2.connect` 默认设置，检查 h2 ALPN。全 200。同一运行时但两套协议实现；不是浏览器实验。

|顺序|协议|各请求 ms|整批 ms|
|---|---|---|---:|
|A|H1|523.1 / 110.6 / 102.4|736.7|
|B|H2|1821.3 / 1626.0 / 1631.3|5080.1|
|B|H2|1738.4 / 1567.9 / 1568.3|4876.9|
|A|H1|520.4 / 102.3 / 100.5|723.3|

- H2 接收流控对照：同一 Node 客户端，按下列顺序开四条新 H2 会话，每批 **两次**串行 GET，均校验 200、长度、哈希。`http2.connect(origin,{settings:{initialWindowSize:w}})`，connect 后 `session.setLocalWindowSize(w)`；流与连接窗口成对调整，不能区分各自贡献。计时自 connect 前至两次读完校验后。`w=1048576,65535,65535,1048576 B` 分别耗时 **748.6、3091.9、3167.0、919.6 ms**。这是小样本复现实验，不是建议全球改成 1 MiB。

## D. 不混入上述比较的历史数值

[V0.2 验收 §3–6](V02_TEST_REPORT.md)：十并发 17.8 MB / 1.47 s ≈ 96.8 Mbps **聚合**；1/5/10 通过是功能结果，不是压测上限。报告列六件约 137 Mbps 又写 3.2–3.4 MB/s，单位不等价，缺少逐件/批次分母，保留为未独立证实。香港→Raw 与回环 8080 不是大陆→香港 HTTPS。其他运营商、晚高峰、H3、同时段服务端资源计数均无本轮有效样本；不报 p95 或全网结论。
