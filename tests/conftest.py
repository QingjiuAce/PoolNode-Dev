"""pytest 共享夹具：临时配置、本地 HTTP 测试服务器、测试资产清单。"""

from __future__ import annotations

import functools
import hashlib
import http.server
import socket
import sys
import threading
import pathlib

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from helpers import make_png  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_config(root: pathlib.Path, **overrides) -> pathlib.Path:
    """生成一套隔离的测试配置（tmp/log/records 全在 pytest tmp 下）。"""
    cfg = {
        "storage_root": str(root / "data"),
        "tmp_dir": str(root / "tmp"),
        "log_dir": str(root / "logs"),
        "records_file": str(root / "logs" / "sync-records.jsonl"),
        "max_file_size_mb": 5,
        "guard_system_disk": False,  # isolated single-volume test fixture
        "allowed_formats": ["png", "jpeg", "gif", "webp"],
        "upstream": {
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "connect_timeout": 5,
            "read_timeout": 10,
            "allow_loopback_http": True,   # 测试专用
        },
    }
    cfg.update(overrides)
    p = root / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _make_server(directory: str, handler_cls) -> http.server.ThreadingHTTPServer:
    return http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)


class LocalImageServer:
    """本地 HTTP 图片源（模拟上游）。支持注入自定义 handler 做重定向等行为。"""

    def __init__(self, directory: pathlib.Path):
        self.directory = str(directory)
        self.handler_cls = functools.partial(_QuietHandler, directory=self.directory)
        self.server = _make_server(self.directory, self.handler_cls)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def restart_with(self, handler_cls) -> None:
        """用自定义 handler 类重启（保持同一端口）。"""
        self.stop()
        self.handler_cls = handler_cls
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), self.handler_cls)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def redirect_to(self, target_url: str) -> None:
        """把本服务器变成 302 跳板，所有 GET 都跳到 target_url。"""
        class Redirect(_QuietHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target_url)
                self.end_headers()

        self.restart_with(Redirect)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def url_for(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{name}"


def make_assets_file(path: pathlib.Path, source_url: str, png_sha: str) -> None:
    """生成测试资产清单：approved/pending/denied 三种状态齐备。"""
    assets = {
        "assets": [
            {
                "asset_id": "test-poc-001",
                "source_url": source_url,
                "relative_path": "pool-tests/test-poc-001.png",
                "permission_status": "approved",
                "permission_evidence": "测试资产（本项目自生成图片，无第三方著作权）",
                "attribution": "Pool PoC 测试素材",
                "license": "MIT (test)",
                "expected_sha256": png_sha,
                "revision": 1,
            },
            {
                "asset_id": "pending-asset",
                "source_url": source_url,
                "relative_path": "pool-tests/pending.png",
                "permission_status": "pending",
                "permission_evidence": "",
                "attribution": "", "license": "",
                "expected_sha256": None, "revision": 1,
            },
            {
                "asset_id": "denied-asset",
                "source_url": source_url,
                "relative_path": "pool-tests/denied.png",
                "permission_status": "denied",
                "permission_evidence": "演示",
                "attribution": "", "license": "",
                "expected_sha256": None, "revision": 1,
            },
            {
                "asset_id": "approved-no-evidence",
                "source_url": source_url,
                "relative_path": "pool-tests/no-evidence.png",
                "permission_status": "approved",
                "permission_evidence": "",
                "attribution": "", "license": "",
                "expected_sha256": None, "revision": 1,
            },
        ]
    }
    path.write_text(yaml.safe_dump(assets), encoding="utf-8")


@pytest.fixture()
def env(tmp_path):
    """隔离环境：源服务器 + 配置 + 白名单 + 测试图片。"""
    src_dir = tmp_path / "src"
    (src_dir / "pool-tests").mkdir(parents=True)
    png = make_png(src_dir / "pool-tests" / "test-poc-001.png", 64, 64, seed=1)

    server = LocalImageServer(src_dir)
    assets_path = tmp_path / "assets.yaml"
    make_assets_file(assets_path, server.url_for("pool-tests/test-poc-001.png"), sha256_of(png))
    config_path = write_config(tmp_path)

    state = type("Env", (), {})()
    state.tmp_path = tmp_path
    state.config_path = config_path
    state.assets_path = assets_path
    state.server = server
    state.png_bytes = png
    state.png_sha = sha256_of(png)
    state.data_dir = tmp_path / "data"
    state.src_dir = src_dir
    yield state
    server.stop()
