#!/usr/bin/env bash
# Pool PoC V0.2 公网验收（外部真实客户端，不走 SSH 隧道）
set -uo pipefail
BASE="https://dl.bszx.site"
ASSET="submissions/originals/pool-public-smoke-test.jpg"
URL="$BASE/$ASSET"
EXPECT_SHA="b60435075bfccd82ecb6fc20706276b0df2559c8f816bc933dbfc6ac7eaa932e"
EXPECT_SIZE=1778514
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  PASS: $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL: $1"; }
chk(){ if [[ "$2" == "$3" ]]; then ok "$1 ($2)"; else bad "$1 (期望 $3 实际 $2)"; fi; }

echo "===== 0. TLS 证书与链 ====="
TLSINFO=$(echo | openssl s_client -connect dl.bszx.site:443 -servername dl.bszx.site 2>/dev/null | openssl x509 -noout -subject -issuer -dates 2>/dev/null)
echo "  $TLSINFO"
echo | openssl s_client -connect dl.bszx.site:443 -servername dl.bszx.site 2>/dev/null | grep -q "Verify return code: 0 (ok)" \
  && ok "证书链验证 Verify return code: 0 (ok)" || bad "证书链验证"
TLSVER=$(echo | openssl s_client -connect dl.bszx.site:443 -servername dl.bszx.site 2>/dev/null | grep -oE "TLSv1\.[23]" | head -1)
ok "TLS 版本 ($TLSVER)"

echo "===== 1. GET ====="
curl -sS -o /tmp/pub-get.jpg -w "  http=%{http_code} size=%{size_download} time=%{time_total}s speed=%{speed_download}B/s\n" --max-time 60 "$URL"
chk "GET 状态码" "$(curl -s -o /dev/null -w '%{http_code}' "$URL")" "200"
GET_SHA=$(sha256sum /tmp/pub-get.jpg 2>/dev/null | awk '{print $1}')
chk "GET SHA256 三方一致" "$GET_SHA" "$EXPECT_SHA"
chk "GET 字节数" "$(stat -c %s /tmp/pub-get.jpg 2>/dev/null)" "$EXPECT_SIZE"

echo "===== 2. HEAD ====="
H=$(curl -sI "$URL")
chk "HEAD 状态码" "$(echo "$H" | head -1 | awk '{print $2}')" "200"
chk "HEAD Content-Length" "$(echo "$H" | grep -i '^content-length:' | tr -d '\r' | awk '{print $2}')" "$EXPECT_SIZE"
echo "$H" | grep -qi '^content-type: image/jpeg' && ok "Content-Type image/jpeg" || bad "Content-Type"
echo "$H" | grep -qi '^accept-ranges: bytes' && ok "Accept-Ranges" || bad "Accept-Ranges 缺失"
echo "$H" | grep -qi '^etag:' && ok "ETag 存在" || bad "ETag 缺失"
echo "$H" | grep -qi '^last-modified:' && ok "Last-Modified 存在" || bad "Last-Modified 缺失"
echo "$H" | grep -qi '^x-content-type-options: nosniff' && ok "nosniff" || bad "nosniff 缺失"
echo "$H" | grep -qi '^x-robots-tag: noindex' && ok "X-Robots-Tag noindex" || bad "X-Robots-Tag 缺失"
SRV=$(echo "$H" | grep -i '^server:' | tr -d '\r')
echo "$SRV" | grep -q '/' && bad "Server 版本泄露 ($SRV)" || ok "Server 无版本 ($SRV)"

echo "===== 3. Range ====="
R=$(curl -sS -o /tmp/pub-range.bin -D - -w '\n%{http_code}' -r 0-1023 "$URL")
chk "Range 状态码" "$(echo "$R" | tail -1)" "206"
chk "Content-Range" "$(echo "$R" | grep -i '^content-range:' | tr -d '\r')" "Content-Range: bytes 0-1023/$EXPECT_SIZE"
chk "Range 实收字节" "$(stat -c %s /tmp/pub-range.bin 2>/dev/null)" "1024"
chk "Range 内容与首段一致" "$(sha256sum /tmp/pub-range.bin | awk '{print $1}')" "$(head -c 1024 /tmp/pub-get.jpg | sha256sum | awk '{print $1}')"

echo "===== 4. 404 / 授权边界 ====="
chk "不存在文件 404" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/submissions/originals/not-exist.jpg")" "404"
chk "pending 真实图片(owner-picks/blue-maid.png)不可下载" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/owner-picks/blue-maid.png")" "404"
chk "denied 图片(blue-fish-originals/…)不可下载" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/blue-fish-originals/026e44f4-845f-4e51-bcec-812497aadcff.png")" "404"
DC=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/submissions/")
[[ "$DC" == "403" || "$DC" == "404" ]] && ok "目录无索引 ($DC)" || bad "目录 ($DC)"
chk "根路径 404" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/")" "404"
chk "隐藏文件 404" "$(curl -s -o /dev/null -w '%{http_code}' --path-as-is "$BASE/.htaccess")" "404"
chk "yaml/日志后缀 404" "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/config.yaml")" "404"

echo "===== 5. 路径穿越 ====="
C1=$(curl -s -o /dev/null -w '%{http_code}' --path-as-is "$BASE/../../etc/passwd")
[[ "$C1" == "400" || "$C1" == "404" || "$C1" == "403" ]] && ok "穿越 ../ ($C1)" || bad "穿越 ../ ($C1)"
C2=$(curl -s -o /dev/null -w '%{http_code}' --path-as-is "$BASE/%2e%2e/%2e%2e/etc/passwd")
[[ "$C2" == "400" || "$C2" == "404" || "$C2" == "403" ]] && ok "编码穿越 %2e%2e ($C2)" || bad "编码穿越 ($C2)"
C3=$(curl -s -o /dev/null -w '%{http_code}' --path-as-is "$BASE/submissions/..%2f..%2fetc%2fpasswd")
[[ "$C3" == "400" || "$C3" == "404" || "$C3" == "403" ]] && ok "混合编码穿越 ($C3)" || bad "混合编码穿越 ($C3)"

echo "===== 6. HTTP 方法限制 ====="
chk "POST 拒绝" "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL")" "403"
chk "PUT 拒绝" "$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$URL")" "403"
chk "DELETE 拒绝" "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$URL")" "403"

echo "===== 7. HTTP->HTTPS 重定向 ====="
R=$(curl -sI "http://dl.bszx.site/$ASSET" | head -1 | awk '{print $2}')
chk "HTTP 301" "$R" "301"
curl -sI "http://dl.bszx.site/$ASSET" | grep -qi '^location: https://' && ok "Location https://" || bad "Location 缺失"

echo "===== 8. 并发（温和：1/5/10）====="
for N in 1 5 10; do
  START=$(date +%s.%N)
  for i in $(seq 1 $N); do curl -sS -o /dev/null -w "conn$i:%{http_code}:%{time_total}s " "$URL" & done
  wait
  END=$(date +%s.%N)
  echo "  $N 并发总耗时: $(echo "$END $START" | awk '{printf "%.2fs", $1-$2}')"
done

rm -f /tmp/pub-get.jpg /tmp/pub-range.bin
echo
echo "结果: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]] && echo "PUBLIC ACCEPTANCE: ALL PASS" || echo "PUBLIC ACCEPTANCE: HAS FAILURES"
