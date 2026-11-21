"""下载器与端到端 sync 流程测试（本地 HTTP 测试服务器，不出网）。"""

from __future__ import annotations

import http.server
import os
import sys
import pathlib

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from helpers import make_png, sha256_file
from pool.cli import cmd_remove, cmd_sync, cmd_verify
from pool.config import load_config
from pool.downloader import (
    HttpStatusFail,
    RedirectBlocked,
    SizeLimitExceeded,
    check_upstream,
    download_to_file,
)
from pool.permissions import load_assets
from pool.storage import read_records
from conftest import LocalImageServer, write_config


def _reload(cfg_path, assets_path):
    return load_config(cfg_path), load_assets(assets_path)


class HTMLHandler(http.server.SimpleHTTPRequestHandler):
    """返回 HTML 的假上游（模拟恶意/错误内容）。"""

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>not an image</body></html>")

    def log_message(self, *a):
        pass


class TestDownloader:
    def test_download_ok(self, env):
        cfg, _ = _reload(env.config_path, env.assets_path)
        dest = env.tmp_path / "dl" / "out.png"
        dest.parent.mkdir()
        path, ctype, size = download_to_file(
            env.server.url_for("pool-tests/test-poc-001.png"), cfg.upstream, dest, cfg.max_file_size)
        assert size == len(env.png_bytes)
        assert sha256_file(dest) == env.png_sha

    def test_404_classified(self, env):
        cfg, _ = _reload(env.config_path, env.assets_path)
        dest = env.tmp_path / "dl" / "out.png"
        dest.parent.mkdir()
        with pytest.raises(HttpStatusFail) as e:
            download_to_file(env.server.url_for("no-such-file.png"), cfg.upstream, dest, 1024)
        assert e.value.status == 404
        assert not dest.exists()

    def test_size_limit_enforced(self, env, tmp_path):
        """超过上限立即中止，且不留部分文件。"""
        cfg, _ = _reload(env.config_path, env.assets_path)
        dest = tmp_path / "out.png"
        with pytest.raises(SizeLimitExceeded):
            download_to_file(env.server.url_for("pool-tests/test-poc-001.png"),
                             cfg.upstream, dest, 10)
        assert not dest.exists()

    def test_disallowed_host_rejected(self, env, tmp_path):
        cfg, _ = _reload(env.config_path, env.assets_path)
        dest = tmp_path / "out.png"
        with pytest.raises(RedirectBlocked):
            download_to_file("http://evil.example.com/x.png", cfg.upstream, dest, 1024)

    def test_redirect_to_disallowed_host_blocked(self, env):
        """上游 302 跳到白名单外主机时必须中止。"""
        cfg, _ = _reload(env.config_path, env.assets_path)
        env.server.redirect_to("http://not-in-allowlist.example.com/x.png")
        dest = env.tmp_path / "dl2" / "out.png"
        dest.parent.mkdir()
        with pytest.raises(RedirectBlocked):
            download_to_file(env.server.url_for("anything.png"), cfg.upstream, dest, 1024)
        assert not dest.exists()

    def test_redirect_to_allowed_loopback_allowed(self, tmp_path):
        """白名单内主机（回环、不同端口）的重定向应当放行。"""
        src_dir = tmp_path / "src2"
        src_dir.mkdir()
        make_png(src_dir / "real.png", 16, 16, seed=9)
        main = LocalImageServer(src_dir)
        redir = LocalImageServer(src_dir)
        try:
            cfg = load_config(write_config(tmp_path))  # allowed_hosts 含 127.0.0.1
            redir.redirect_to(main.url_for("real.png"))
            dest = tmp_path / "out.png"
            _, _, size = download_to_file(redir.url_for("jump.png"), cfg.upstream, dest, 1024 * 1024)
            assert size > 0
            assert sha256_file(dest) == sha256_file(src_dir / "real.png")
        finally:
            main.stop()
            redir.stop()

    def test_check_upstream_diagnostics(self, env):
        cfg, _ = _reload(env.config_path, env.assets_path)
        d = check_upstream(env.server.url_for("pool-tests/test-poc-001.png"), cfg.upstream)
        assert d["dns"]["ok"] is True
        assert d["tcp"]["ok"] is True
        assert d["get"]["status"] == 200
        assert d["get"]["sample_is_image_magic"] is True
        assert d["head"]["ok"] is True


class TestSyncPipeline:
    def test_sync_publishes_atomically(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        final = env.data_dir / "pool-tests" / "test-poc-001.png"
        assert final.exists()
        assert sha256_file(final) == env.png_sha
        # 权限应为只读
        assert not (final.stat().st_mode & 0o222)
        # 临时区应清空
        assert list((env.tmp_path / "tmp").glob("*.part")) == []
        # 记录应存在
        recs = read_records(cfg)
        assert recs[-1]["result"] == "published"
        assert recs[-1]["sha256"] == env.png_sha
        assert recs[-1]["hash_source"] == "official"

    def test_sync_hash_mismatch_rejected(self, env):
        """官方哈希与实际不符：拒绝发布，不落公开目录。"""
        doc = yaml.safe_load(env.assets_path.read_text(encoding="utf-8"))
        doc["assets"][0]["expected_sha256"] = "0" * 64
        env.assets_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 1
        assert not (env.data_dir / "pool-tests" / "test-poc-001.png").exists()
        recs = read_records(cfg)
        assert recs[-1]["result"] == "rejected"
        assert list((env.tmp_path / "tmp").glob("*.part")) == []

    def test_sync_pending_rejected(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "pending-asset") == 1
        assert not (env.data_dir / "pool-tests" / "pending.png").exists()
        assert read_records(cfg)[-1]["reason"] == "STATUS_PENDING"

    def test_sync_denied_rejected(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "denied-asset") == 1
        assert not (env.data_dir / "pool-tests" / "denied.png").exists()
        assert read_records(cfg)[-1]["reason"] == "STATUS_DENIED"

    def test_sync_approved_without_evidence_rejected(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "approved-no-evidence") == 1
        assert not (env.data_dir / "pool-tests" / "no-evidence.png").exists()
        assert read_records(cfg)[-1]["reason"] == "NO_EVIDENCE"

    def test_sync_unknown_asset_rejected(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "no-such-asset") == 1
        assert read_records(cfg)[-1]["reason"] == "NO_SUCH_ASSET"

    def test_sync_non_image_rejected(self, env):
        """上游返回 HTML 时按格式错误拒绝。"""
        env.server.restart_with(HTMLHandler)
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 1
        assert not (env.data_dir / "pool-tests" / "test-poc-001.png").exists()

    def test_guard_system_disk_rejects_same_device(self, env):
        """guard_system_disk=true 且 storage_root 与系统盘同设备时拒绝同步。"""
        import os
        same_dev = os.stat(env.data_dir.parent).st_dev == os.stat("/").st_dev
        doc = yaml.safe_load(env.config_path.read_text(encoding="utf-8"))
        doc["guard_system_disk"] = True
        env.config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        cfg, assets = _reload(env.config_path, env.assets_path)
        if not same_dev:
            import pytest as _pytest
            _pytest.skip("pytest 临时目录与系统根不同设备，无法模拟掉挂载场景")
        assert cmd_sync(cfg, assets, "test-poc-001") == 1
        assert "系统盘" in read_records(cfg)[-1]["detail"]
        assert not (env.data_dir / "pool-tests" / "test-poc-001.png").exists()

    def test_sync_twice_same_content(self, env):
        """同路径重复 sync 允许（revision 变更场景），内容一致。"""
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        assert sha256_file(env.data_dir / "pool-tests" / "test-poc-001.png") == env.png_sha


class TestVerifyRemove:
    def test_verify_pass_after_sync(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        assert cmd_verify(cfg, assets, "test-poc-001") == 0

    def test_verify_fails_before_sync(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_verify(cfg, assets, "test-poc-001") == 1

    def test_verify_detects_tampering(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        target = env.data_dir / "pool-tests" / "test-poc-001.png"
        # 模拟磁盘文件被篡改（绕过 pool 工具直接写）
        os.chmod(target, 0o644)
        target.write_bytes(b"tampered")
        os.chmod(target, 0o444)
        assert cmd_verify(cfg, assets, "test-poc-001") == 1

    def test_remove_deletes_file_and_verify_fails(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        assert cmd_remove(cfg, assets, "test-poc-001") == 0
        assert not (env.data_dir / "pool-tests" / "test-poc-001.png").exists()
        assert cmd_verify(cfg, assets, "test-poc-001") == 1
        assert read_records(cfg)[-1]["result"] == "removed"

    def test_remove_only_touches_target(self, env):
        """删除失败/其他资产不影响已同步文件。"""
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_sync(cfg, assets, "test-poc-001") == 0
        assert cmd_remove(cfg, assets, "denied-asset") == 1  # 文件不存在 → 明确报错
        assert (env.data_dir / "pool-tests" / "test-poc-001.png").exists()

    def test_remove_unknown_asset_rejected(self, env):
        cfg, assets = _reload(env.config_path, env.assets_path)
        assert cmd_remove(cfg, assets, "unknown-id") == 1
