#!/usr/bin/env python3
"""生成确定性测试 JPEG（自生成素材，无第三方版权内容）。

依赖 Pillow（服务器: apt install python3-pil；本地: pip install pillow）。
同平台同参数输出字节一致；SHA256 用于 assets.yaml 的 expected_sha256 登记。

用法: python3 gen-test-jpeg.py <输出路径> [宽] [高] [seed] [质量]
"""

import hashlib
import random
import sys

from PIL import Image


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "pool-public-smoke-test.jpg"
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 1600
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 42
    quality = int(sys.argv[5]) if len(sys.argv) > 5 else 90

    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    # 噪声 + 渐变叠加，保证 JPEG 压缩后有足够字节量（便于带宽观察）
    data = [
        ((x * 255 // max(w - 1, 1) + rnd.randrange(64)) % 256,
         (y * 255 // max(h - 1, 1) + rnd.randrange(64)) % 256,
         rnd.randrange(256))
        for y in range(h)
        for x in range(w)
    ]
    img.putdata(data)
    img.save(out, "JPEG", quality=quality, optimize=False)

    with open(out, "rb") as f:
        blob = f.read()
    print(f"written : {out}")
    print(f"size    : {len(blob)} bytes")
    print(f"sha256  : {hashlib.sha256(blob).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
