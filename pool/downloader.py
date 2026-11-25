"""上游 HTTPS 下载与连通性诊断。

CLI 从资产白名单选择初始 URL；本模块负责连接与响应，不负责授权或发布。
传输层独立复核允许的主机和协议，下载的每个重定向也必须通过复核。
HTTPS 使用系统 CA 验证；只在显式测试配置中允许回环 HTTP。
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import nullcontext
from pathlib import Path
from typing import BinaryIO

from . import __version__
from .config import UpstreamConfig

CHUNK_SIZE = 256 * 1024
SAMPLE_BYTES = 256 * 1024  # check-upstream 采样的字节数

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def user_agent(purpose: str) -> str:
    """HTTP User-Agent derived from the single package version source."""
    return f"pool-poc/{__version__} (+{purpose})"


class UpstreamError(Exception):
    """上游访问失败。kind 用于区分失败类别，不允许统一输出为“下载失败”。"""

    kind = "unknown"

    def __init__(self, detail: str):
        super().__init__(f"[{self.kind}] {detail}")
        self.detail = detail


class DNSFail(UpstreamError):
    kind = "dns_fail"


class TCPFail(UpstreamError):
    kind = "tcp_fail"


class TLSFail(UpstreamError):
    kind = "tls_fail"


class TimeoutFail(UpstreamError):
    kind = "timeout"


class HttpStatusFail(UpstreamError):
    kind = "http_status"

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


class NonImageFail(UpstreamError):
    kind = "non_image"


class RedirectBlocked(UpstreamError):
    kind = "redirect_blocked"


class SizeLimitExceeded(UpstreamError):
    kind = "size_limit"


def _url_allowed(url: str, cfg: UpstreamConfig) -> urllib.parse.ParseResult:
    """校验 scheme 与主机；每跳（含初始 URL 与每个重定向）都必须过这一关。"""
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    https_ok = u.scheme == "https" and host in cfg.allowed_hosts and u.port in (None, 443)
    loopback_ok = (
        cfg.allow_loopback_http
        and u.scheme == "http"
        and host in LOOPBACK_HOSTS
        and u.port is not None
    )
    if not (https_ok or loopback_ok):
        raise RedirectBlocked(f"不允许的 URL: {url!r}（主机 {host!r} 不在白名单或 scheme 非法）")
    if u.username or u.password or u.query or u.fragment:
        raise RedirectBlocked(f"不允许携带 userinfo/query/fragment 的 URL: {url!r}")
    return u


def _classify_socket_error(e: Exception, stage: str) -> UpstreamError:
    if isinstance(e, socket.gaierror):
        return DNSFail(f"{stage}: 域名解析失败: {e}")
    if isinstance(e, socket.timeout) or isinstance(e, TimeoutError):
        return TimeoutFail(f"{stage}: 超时")
    if isinstance(e, ssl.SSLError):
        return TLSFail(f"{stage}: TLS 失败: {e}")
    return TCPFail(f"{stage}: {type(e).__name__}: {e}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """逐跳校验重定向目标，跳出白名单立即失败。"""

    def __init__(self, cfg: UpstreamConfig):
        self._cfg = cfg

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url_allowed(newurl, self._cfg)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener(cfg: UpstreamConfig) -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()  # 系统 CA，强制证书校验
    return urllib.request.build_opener(
        _SafeRedirectHandler(cfg),
        urllib.request.HTTPSHandler(context=ctx),
    )


def _is_rejected_http_status(status: int) -> bool:
    return status in (403, 404, 410, 429) or 500 <= status < 600


def check_upstream(source_url: str, cfg: UpstreamConfig) -> dict:
    """分阶段诊断连接；只取样，不落盘或跟随 HTTP 重定向。

    返回 DNS、TCP、TLS、HEAD 和 GET 的诊断字典。已分类的网络失败
    抛出对应的 UpstreamError；HEAD 失败则留在诊断结果中。
    """
    u = _url_allowed(source_url, cfg)
    host = u.hostname or ""
    port = u.port or 443

    result: dict = {"url": source_url, "host": host}

    # --- DNS ---
    t0 = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise DNSFail(f"域名解析失败: {host}: {e}") from e
    except OSError as e:
        raise _classify_socket_error(e, "dns") from e
    result["dns"] = {
        "ok": True,
        "addresses": sorted({i[4][0] for i in infos}),
        "ms": round((time.monotonic() - t0) * 1000, 1),
    }
    ip = infos[0][4][0]

    # --- TCP ---
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((ip, port), timeout=cfg.connect_timeout)
    except OSError as e:
        raise _classify_socket_error(e, f"tcp({ip}:{port})") from e
    result["tcp"] = {"ok": True, "peer": f"{ip}:{port}", "ms": round((time.monotonic() - t0) * 1000, 1)}

    # --- TLS（回环 http 测试无此阶段）---
    if u.scheme == "https":
        t0 = time.monotonic()
        try:
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(sock, server_hostname=host)
        except OSError as e:
            sock.close()
            raise _classify_socket_error(e, f"tls({host})") from e
        result["tls"] = {
            "ok": True,
            "version": tls.version(),
            "cipher": tls.cipher()[0],
            "ms": round((time.monotonic() - t0) * 1000, 1),
        }
        conn = http.client.HTTPSConnection(host, port, timeout=cfg.read_timeout, context=ctx)
        sock.close()
    else:
        result["tls"] = {"ok": False, "note": "loopback http（仅测试配置）"}
        conn = http.client.HTTPConnection(host, port, timeout=cfg.read_timeout)

    # --- HEAD（如上游支持）---
    head_info: dict = {"ok": False}
    try:
        conn.request("HEAD", u.path, headers={"User-Agent": user_agent("check-upstream")})
        resp = conn.getresponse()
        resp.read()
        head_info = {
            "ok": True,
            "status": resp.status,
            "content_type": resp.getheader("Content-Type"),
            "content_length": resp.getheader("Content-Length"),
        }
    except (OSError, http.client.HTTPException) as e:
        head_info["error"] = f"{type(e).__name__}: {e}"
    result["head"] = head_info

    # --- GET 取样 ---
    t0 = time.monotonic()
    try:
        conn.request("GET", u.path, headers={"User-Agent": user_agent("check-upstream")})
        resp = conn.getresponse()
    except OSError as e:
        conn.close()
        raise _classify_socket_error(e, "http-get") from e

    status = resp.status
    result["http"] = {"status": status, "reason": resp.reason,
                      "content_type": resp.getheader("Content-Type"),
                      "content_length": resp.getheader("Content-Length")}
    if _is_rejected_http_status(status):
        conn.close()
        raise HttpStatusFail(status, f"上游返回 HTTP {status} {resp.reason}")

    ttfb = None
    sample = bytearray()
    while len(sample) < SAMPLE_BYTES:
        chunk = resp.read(CHUNK_SIZE)
        if not chunk:
            break
        if ttfb is None:
            ttfb = time.monotonic() - t0
        sample.extend(chunk)
    total = time.monotonic() - t0
    conn.close()

    if ttfb is None:
        raise TimeoutFail("未在超时时间内收到首字节")
    fmt_ok = sample[:16] != b"" and (
        sample.startswith(b"\x89PNG")
        or sample.startswith(b"\xff\xd8\xff")
        or sample.startswith((b"GIF87a", b"GIF89a"))
        or (sample[:4] == b"RIFF" and sample[8:12] == b"WEBP")
    )
    result["get"] = {
        "status": status,
        "ttfb_ms": round(ttfb * 1000, 1),
        "total_ms": round(total * 1000, 1),
        "sample_bytes": len(sample),
        "sample_speed_bps": round(len(sample) / total, 0) if total > 0 else None,
        "sample_is_image_magic": fmt_ok,
    }
    return result


def download_to_file(source_url: str, cfg: UpstreamConfig, dest: Path | BinaryIO,
                     max_size: int) -> tuple[Path | BinaryIO, str, int]:
    """流式下载到暂存路径或由调用方持有的已打开文件。"""
    _url_allowed(source_url, cfg)
    req = urllib.request.Request(source_url, headers={"User-Agent": user_agent("sync")})
    opener = _build_opener(cfg)

    try:
        resp = opener.open(req, timeout=cfg.read_timeout)
    except urllib.error.HTTPError as e:
        raise HttpStatusFail(e.code, f"上游返回 HTTP {e.code} {e.reason}（{source_url}）") from e
    except urllib.error.URLError as e:
        # URLError.reason 可能是 socket 层错误，细分类别
        raise _classify_socket_error(e.reason, "open") from e
    except OSError as e:
        raise _classify_socket_error(e, "open") from e

    with resp:
        status = resp.status
        if _is_rejected_http_status(status):
            raise HttpStatusFail(status, f"上游返回 HTTP {status}")
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        content_length = resp.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > max_size:
            raise SizeLimitExceeded(
                f"上游声明 Content-Length={content_length} 超过上限 {max_size}"
            )

        written = 0
        try:
            with (open(dest, "wb") if isinstance(dest, Path) else nullcontext(dest)) as f:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_size:
                        raise SizeLimitExceeded(
                            f"下载字节超过上限 {max_size}（已写 {written}）"
                        )
                    f.write(chunk)
        except (socket.timeout, TimeoutError) as e:
            if isinstance(dest, Path):
                dest.unlink(missing_ok=True)
            raise TimeoutFail(f"下载中途超时（已写 {written}）") from e
        except Exception:
            if isinstance(dest, Path):
                dest.unlink(missing_ok=True)
            raise
    return dest, content_type, written
