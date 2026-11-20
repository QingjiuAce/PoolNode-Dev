"""SHA256 计算与格式检查测试。"""

from __future__ import annotations

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from helpers import make_png, sha256_file
from pool.validator import FormatError, check_format, detect_format, looks_like_svg


class TestSha256:
    def test_known_vector(self):
        import hashlib
        assert sha256_file_of_bytes(b"hello") == hashlib.sha256(b"hello").hexdigest()

    def test_file_roundtrip(self, tmp_path):
        p = tmp_path / "a.png"
        data = make_png(p, 16, 16, seed=3)
        assert sha256_file(p) == __import__("hashlib").sha256(data).hexdigest()

    def test_deterministic_generation(self, tmp_path):
        """同一参数生成的图片字节级一致（同步可复现的前提）。"""
        p1, p2 = tmp_path / "1.png", tmp_path / "2.png"
        d1 = make_png(p1, 32, 32, seed=7)
        d2 = make_png(p2, 32, 32, seed=7)
        assert d1 == d2


def sha256_file_of_bytes(data: bytes) -> str:
    import hashlib
    import tempfile, os
    fd, name = tempfile.mkstemp()
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return sha256_file(__import__("pathlib").Path(name))
    finally:
        os.unlink(name)


class TestDetectFormat:
    def test_png(self):
        assert detect_format(b"\x89PNG\r\n\x1a\n" + b"x" * 32) == "png"

    def test_jpeg(self):
        assert detect_format(b"\xff\xd8\xff\xe0" + b"x" * 32) == "jpeg"

    def test_gif87(self):
        assert detect_format(b"GIF87a" + b"x" * 32) == "gif"

    def test_gif89(self):
        assert detect_format(b"GIF89a" + b"x" * 32) == "gif"

    def test_webp(self):
        assert detect_format(b"RIFF\x00\x00\x10\x00WEBPVP8 " + b"x" * 32) == "webp"

    def test_unknown(self):
        assert detect_format(b"MZ\x90\x00" + b"x" * 32) is None  # PE 可执行
        assert detect_format(b"%PDF-1.7 " + b"x" * 32) is None


class TestCheckFormat:
    ALLOWED = frozenset({"png", "jpeg", "gif", "webp"})

    def test_real_png_passes(self, tmp_path):
        p = tmp_path / "t.png"
        make_png(p, 8, 8)
        assert check_format(p, self.ALLOWED) == "png"

    def test_svg_rejected(self, tmp_path):
        p = tmp_path / "t.svg"
        p.write_bytes(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>')
        with pytest.raises(FormatError, match="SVG"):
            check_format(p, self.ALLOWED)

    def test_disguised_svg_rejected(self, tmp_path):
        """伪装成 .png 的 SVG 也必须拒绝。"""
        p = tmp_path / "t.png"
        p.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>')
        with pytest.raises(FormatError, match="SVG"):
            check_format(p, self.ALLOWED)

    def test_html_rejected(self, tmp_path):
        p = tmp_path / "t.png"
        p.write_bytes(b"<!DOCTYPE html><html><body>x</body></html>")
        with pytest.raises(FormatError):
            check_format(p, self.ALLOWED)

    def test_empty_rejected(self, tmp_path):
        p = tmp_path / "t.png"
        p.write_bytes(b"")
        with pytest.raises(FormatError, match="为空"):
            check_format(p, self.ALLOWED)

    def test_restricted_format_list(self, tmp_path):
        p = tmp_path / "t.gif"
        p.write_bytes(b"GIF89a" + b"\x00" * 64)
        with pytest.raises(FormatError, match="不在允许列表"):
            check_format(p, frozenset({"png"}))
