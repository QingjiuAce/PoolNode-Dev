"""路径校验、文件格式检查与 SHA256。

双层路径防御：
1. 字符级白名单（正则 + 段级规则）；
2. 归一化 + realpath 后强制落在存储根内。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import posixpath
import re

# 段级规则：不允许空段、'.'、'..'、以点开头的隐藏段；总深度受限
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_DEPTH = 16
MAX_PATH_LEN = 1024

MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
MAGIC_JPEG = b"\xff\xd8\xff"
MAGIC_GIF87 = b"GIF87a"
MAGIC_GIF89 = b"GIF89a"


class PathError(Exception):
    """relative_path 非法或逃逸出存储根。"""


class FormatError(Exception):
    """文件真实格式不在允许列表（含 SVG 拒绝）。"""


def validate_relative_path(rel: str) -> str:
    """校验仓库相对路径。通过时原样返回；不通过抛 PathError。"""
    if not rel or not isinstance(rel, str):
        raise PathError("relative_path 不能为空")
    if len(rel) > MAX_PATH_LEN:
        raise PathError(f"relative_path 过长（>{MAX_PATH_LEN}）")
    if rel.startswith("/") or "\\" in rel or "\x00" in rel:
        raise PathError(f"relative_path 非法（绝对路径/反斜杠/空字节）: {rel!r}")
    if rel != rel.strip() or any(c.isspace() for c in rel):
        raise PathError(f"relative_path 不允许空白字符: {rel!r}")
    if rel != posixpath.normpath(rel):
        raise PathError(f"relative_path 必须是规范化路径（禁止 ./ 与 //）: {rel!r}")
    segments = rel.split("/")
    if len(segments) > MAX_DEPTH:
        raise PathError(f"relative_path 层级超过 {MAX_DEPTH}")
    for seg in segments:
        if not SEGMENT_RE.match(seg):
            raise PathError(f"relative_path 含非法段 {seg!r}（禁止 '..'、隐藏段、特殊字符）")
    return rel


def resolve_safe(storage_root: pathlib.Path, rel: str) -> pathlib.Path:
    """把 relative_path 解析为存储根内的物理路径，并复核真实路径仍在根内。

    即使 rel 通过了字符校验，仍用 realpath 防御符号链接逃逸。
    """
    validate_relative_path(rel)
    root = storage_root.resolve()
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathError(f"解析后的路径逃逸出存储根: {rel!r} -> {candidate}")
    return candidate


def detect_format(head: bytes) -> str | None:
    """按 magic bytes 判定格式；无法识别返回 None。只看前 32 字节即可。"""
    if head.startswith(MAGIC_PNG):
        return "png"
    if head.startswith(MAGIC_JPEG):
        return "jpeg"
    if head.startswith(MAGIC_GIF87) or head.startswith(MAGIC_GIF89):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def looks_like_svg(head: bytes) -> bool:
    """识别 SVG 文本特征（XML 声明 / svg 标签），即便伪装成图片扩展名也拒绝。"""
    sample = head[:512].lstrip().lower()
    return sample.startswith((b"<?xml", b"<svg", b"<!--")) and b"<svg" in head[:512].lower()


def check_format(file_path: pathlib.Path, allowed_formats: frozenset[str]) -> str:
    """检查文件真实格式。返回格式名；SVG 显式拒绝；未知/不允许均抛 FormatError。"""
    with open(file_path, "rb") as f:
        head = f.read(512)
    if not head:
        raise FormatError("文件为空")
    if looks_like_svg(head):
        raise FormatError("检测到 SVG 内容：SVG 未获准通过 Pool 分发（脚本注入风险）")
    fmt = detect_format(head)
    if fmt is None:
        raise FormatError(f"无法识别的文件格式（magic bytes: {head[:16].hex()}）")
    if fmt not in allowed_formats:
        raise FormatError(f"格式 {fmt} 不在允许列表 {sorted(allowed_formats)} 中")
    return fmt


def sha256_file(path: pathlib.Path, chunk_size: int = 1024 * 1024) -> str:
    """流式计算 SHA256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsync_dir(path: pathlib.Path) -> None:
    """fsync 目录项，保证原子移动后的元数据落盘（Windows 上可能不支持，忽略）。"""
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
