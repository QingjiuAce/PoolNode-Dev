#!/usr/bin/env bash
# =====================================================================
# Pool PoC V0.1 — HTTP 面冒烟测试（在服务器本机运行）
#
# 用法: ./smoke-test.sh [BASE_URL]
#   默认 BASE_URL=http://127.0.0.1:8080（回环 vhost）
#   需先 sync 过测试资产 test-poc-001（pool-tests/test-poc-001.png）
# =====================================================================
set -uo pipefail

BASE="${1:-http://127.0.0.1:8080}"
ASSET_PATH="pool-tests/test-poc-001.png"
URL="$BASE/$ASSET_PATH"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  PASS: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL: $1"; }
check(){ if [[ $2 == "$3" ]]; then ok "$1 ($2)"; else bad "$1 (期望 $3 实际 $2)"; fi; }

echo "==> Pool 冒烟测试: $BASE"

# 1. GET 200 + Content-Type + 长度
H=$(curl -sS -o /tmp/pool-smoke-body -D - -w '\n%{http_code}' "$URL")
CODE=$(echo "$H" | tail -1)
check "GET 状态码" "$CODE" "200"
CT=$(echo "$H" | grep -i '^content-type:' | tr -d '\r' | awk '{print $2}')
[[ "$CT" == image/png ]] && ok "Content-Type image/png" || bad "Content-Type ($CT)"
LEN=$(echo "$H" | grep -i '^content-length:' | tr -d '\r' | awk '{print $2}')
[[ "$LEN" =~ ^[0-9]+$ ]] && [[ "$LEN" -gt 0 ]] && ok "Content-Length 存在 ($LEN)" || bad "Content-Length ($LEN)"

# 2. SHA256 一致性（与同步记录比对由 verify 命令做；此处检查可重复下载一致）
SHA1=$(sha256sum /tmp/pool-smoke-body | awk '{print $1}')
curl -sS -o /tmp/pool-smoke-body2 "$URL"
SHA2=$(sha256sum /tmp/pool-smoke-body2 | awk '{print $1}')
check "两次下载 SHA256 一致" "$SHA1" "$SHA2"

# 3. HEAD
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -I "$URL")
check "HEAD 状态码" "$CODE" "200"

# 4. Accept-Ranges / ETag / Last-Modified
curl -sS -D - -o /dev/null "$URL" | grep -qi '^accept-ranges: bytes' && ok "Accept-Ranges: bytes" || bad "Accept-Ranges 缺失"
curl -sS -D - -o /dev/null "$URL" | grep -qi '^etag:' && ok "ETag 存在" || bad "ETag 缺失"
curl -sS -D - -o /dev/null "$URL" | grep -qi '^last-modified:' && ok "Last-Modified 存在" || bad "Last-Modified 缺失"

# 5. Range: 0-1023 → 206 + Content-Range + 1024 字节
RESP=$(curl -sS -o /tmp/pool-smoke-range -D - -w '\n%{http_code}' -r 0-1023 "$URL")
CODE=$(echo "$RESP" | tail -1)
check "Range 状态码" "$CODE" "206"
CR=$(echo "$RESP" | grep -i '^content-range:' | tr -d '\r')
[[ "$CR" == *"bytes 0-1023/"* ]] && ok "Content-Range 正确 ($CR)" || bad "Content-Range ($CR)"
RSIZE=$(stat -c %s /tmp/pool-smoke-range 2>/dev/null || stat -f %z /tmp/pool-smoke-range)
check "Range 实际字节数" "$RSIZE" "1024"

# 6. Range 越界 → 416
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -r 999999999-999999999 "$URL")
[[ "$CODE" == "416" ]] && ok "越界 Range 返回 416" || bad "越界 Range ($CODE)"

# 7. 404
CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/pool-tests/not-exist.png")
check "不存在文件 404" "$CODE" "404"

# 8. 目录索引关闭
CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/pool-tests/")
[[ "$CODE" == "403" || "$CODE" == "404" ]] && ok "目录无索引 ($CODE)" || bad "目录 ($CODE)"
CODE=$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/")
[[ "$CODE" == "404" ]] && ok "根路径 404" || bad "根路径 ($CODE)"

# 9. 路径穿越
CODE=$(curl -sS -o /dev/null -w '%{http_code}' --path-as-is "$BASE/../etc/passwd")
[[ "$CODE" == "404" || "$CODE" == "400" || "$CODE" == "403" ]] && ok "路径穿越被拒 ($CODE)" || bad "路径穿越 ($CODE)"
CODE=$(curl -sS -o /dev/null -w '%{http_code}' --path-as-is "$BASE/%2e%2e/etc/passwd")
[[ "$CODE" == "404" || "$CODE" == "400" || "$CODE" == "403" ]] && ok "编码穿越被拒 ($CODE)" || bad "编码穿越 ($CODE)"

# 10. 非法方法
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$URL")
[[ "$CODE" == "403" || "$CODE" == "405" ]] && ok "POST 被拒 ($CODE)" || bad "POST ($CODE)"
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X PUT "$URL")
[[ "$CODE" == "403" || "$CODE" == "405" ]] && ok "PUT 被拒 ($CODE)" || bad "PUT ($CODE)"
CODE=$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE "$URL")
[[ "$CODE" == "403" || "$CODE" == "405" ]] && ok "DELETE 被拒 ($CODE)" || bad "DELETE ($CODE)"

# 11. 隐藏文件
CODE=$(curl -sS -o /dev/null -w '%{http_code}' --path-as-is "$BASE/.htaccess")
check "隐藏文件 404" "$CODE" "404"

# 12. 响应头
curl -sS -D - -o /dev/null "$URL" | grep -qi '^x-content-type-options: nosniff' && ok "nosniff 头" || bad "nosniff 缺失"
SRV=$(curl -sS -D - -o /dev/null "$URL" | grep -i '^server:' | tr -d '\r')
echo "$SRV" | grep -q '/' && bad "Server 头泄露版本 ($SRV)" || ok "Server 头无版本 ($SRV)"

rm -f /tmp/pool-smoke-body /tmp/pool-smoke-body2 /tmp/pool-smoke-range
echo
echo "结果: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]] && echo "SMOKE TEST: ALL PASS" || exit 1
