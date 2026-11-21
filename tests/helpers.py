"""测试辅助：确定性测试图片生成（纯标准库，无第三方依赖）。

生成的 PNG 字节级确定（同参数同字节），便于 SHA256 断言。
"""

from __future__ import annotations

import struct
import zlib
import hashlib


def make_png(path, width: int = 64, height: int = 64, seed: int = 0) -> bytes:
    """生成一张合法的 RGB PNG，写入 path 并返回其字节内容。"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None)
        for x in range(width):
            raw.extend(bytes(((x * 3 + seed) % 256, (y * 5 + seed * 7) % 256, (x ^ y) % 256)))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return png


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
