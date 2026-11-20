"""路径校验与目录穿越测试。"""

from __future__ import annotations

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pool.validator import PathError, resolve_safe, validate_relative_path


class TestValidateRelativePath:
    @pytest.mark.parametrize("rel", [
        "owner-picks/blue-maid.png",
        "dist/submissions/originals/miku/img_01.png",
        "blue-fish-originals/026e44f4-845f-4e51-bcec-812497aadcff.png",
    ])
    def test_valid_paths(self, rel):
        assert validate_relative_path(rel) == rel

    @pytest.mark.parametrize("rel", [
        "../etc/passwd",                       # 经典穿越
        "a/../../etc/passwd",                  # 中段穿越
        "..%2fetc%2fpasswd",                   # % 非法字符（不在白名单）
        "/etc/passwd",                         # 绝对路径
        r"owner-picks\..\..\windows\temp.png", # 反斜杠
        "owner-picks/../../owner-picks/x.png", # 归一化改变路径
        "./owner-picks/x.png",                 # ./ 前缀
        "owner-picks//x.png",                  # 双斜杠
        "owner-picks/ ",                       # 尾随空白
        " owner-picks/x.png",                  # 前导空白
        ".hidden/x.png",                       # 隐藏段
        "a/../b.png",                          # 归一化改变
        "a/b/c/d/e/f/g/h/i/j/k/l/m/n/o/p/q.png", # 超过 16 层（17 段）
        "",                                    # 空
        "x\x00.png",                           # 空字节
        "..",                                  # 纯点段
        "a/..",                                # 尾部点段
    ])
    def test_invalid_paths(self, rel):
        with pytest.raises(PathError):
            validate_relative_path(rel)


class TestResolveSafe:
    def test_normal_resolution(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        p = resolve_safe(root, "owner-picks/x.png")
        assert p == (root / "owner-picks" / "x.png").resolve()

    def test_traversal_blocked(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        with pytest.raises(PathError):
            resolve_safe(root, "../outside.png")

    def test_root_itself_rejected_as_file_target(self, tmp_path):
        root = tmp_path / "data"
        root.mkdir()
        with pytest.raises(PathError):
            resolve_safe(root, ".")

    def test_symlink_escape_blocked(self, tmp_path):
        """符号链接逃逸防御：存储根内的符号链接指向根外时必须拒绝。"""
        import os
        if os.name == "nt":
            pytest.skip("Windows 创建符号链接需要特权")
        root = tmp_path / "data"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "evil"
        link.symlink_to(outside)
        with pytest.raises(PathError):
            resolve_safe(root, "evil/x.png")
