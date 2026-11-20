#!/usr/bin/env python3
"""生成确定性测试 PNG（纯标准库，同参数同字节）。

用法: python3 gen-test-image.py <输出路径> [宽] [高] [seed]
同时打印 SHA256 与大小，供 assets.yaml 登记 expected_sha256 或测试比对。
"""

import hashlib
import struct
import sys
import zlib


def make_png_bytes(width: int = 64, height: int = 64, seed: int = 0) -> bytes:
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            raw.extend(bytes(((x * 3 + seed) % 256, (y * 5 + seed * 7) % 256, (x ^ y) % 256)))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "test-poc-001.png"
    w = int(sys.argv[2]) if len(sys.argv) > 2 else 64
    h = int(sys.argv[3]) if len(sys.argv) > 3 else 64
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1

    data = make_png_bytes(w, h, seed)
    with open(out, "wb") as f:
        f.write(data)
    print(f"written : {out}")
    print(f"size    : {len(data)} bytes")
    print(f"sha256  : {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
