"""Offline compatibility checks for CLI startup, downloads, and verification."""

from __future__ import annotations

import io
import ssl
import urllib.request

import pytest
import yaml

from conftest import write_config
from helpers import make_png, sha256_of
from pool import cli, downloader
from pool.config import UpstreamConfig, load_config
from pool.storage import read_records


@pytest.mark.parametrize(
    ("argv", "code", "stream", "marker"),
    [
        (["--help"], 0, "out", "check-upstream"),
        (["verify"], 2, "err", "asset_id"),
    ],
)
def test_cli_parser_exits_before_loading_config(argv, code, stream, marker, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    assert exc.value.code == code
    assert marker in getattr(capsys.readouterr(), stream)


@pytest.mark.parametrize("bad_input", ["config", "manifest"])
def test_cli_reports_startup_errors(tmp_path, monkeypatch, capsys, bad_input):
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    cfg_path = tmp_path / "config.yaml"
    if bad_input == "manifest":
        write_config(tmp_path)
        (tmp_path / "assets.yaml").write_text("assets: {}\n", encoding="utf-8")

    assert cli.main(["--config", str(cfg_path), "verify", "missing-asset"]) == 2
    assert "CONFIG ERROR:" in capsys.readouterr().err


@pytest.mark.parametrize("use_override", [False, True], ids=["sibling", "override"])
def test_cli_loads_manifest_from_config_directory_or_override(
    tmp_path, monkeypatch, capsys, use_override
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    cfg_path = write_config(config_dir)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    if use_override:
        # A valid override must win even if the sibling manifest exists but is invalid.
        (config_dir / "assets.yaml").write_text("assets: {}\n", encoding="utf-8")
        manifest = tmp_path / "custom-assets.yaml"
        monkeypatch.setenv("POOL_POC_ASSETS", str(manifest))
    else:
        manifest = config_dir / "assets.yaml"
    manifest.write_text("assets: []\n", encoding="utf-8")

    assert cli.main(["--config", str(cfg_path), "verify", "missing-asset"]) == 1
    assert "NO_SUCH_ASSET" in capsys.readouterr().err


def test_download_opener_requires_certificate_and_hostname_verification():
    cfg = UpstreamConfig(allowed_hosts=("upstream.example.test",))
    opener = downloader._build_opener(cfg)
    https_handler = next(
        handler for handler in opener.handlers
        if isinstance(handler, urllib.request.HTTPSHandler)
    )
    assert https_handler._context.verify_mode == ssl.CERT_REQUIRED
    assert https_handler._context.check_hostname


def test_download_removes_partial_file_when_stream_exceeds_limit(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "image/png"}

    dest = tmp_path / "download.part"
    cfg = UpstreamConfig(allowed_hosts=("upstream.example.test",))

    class Opener:
        def open(self, request, timeout):
            return Response(b"12345678")

    monkeypatch.setattr(downloader, "_build_opener", lambda cfg: Opener())
    monkeypatch.setattr(downloader, "CHUNK_SIZE", 4)

    with pytest.raises(downloader.SizeLimitExceeded):
        downloader.download_to_file(
            "https://upstream.example.test/image.png", cfg, dest, max_size=6
        )

    assert not dest.exists()


def test_verify_uses_recorded_local_hash_when_official_hash_is_absent(
    tmp_path, monkeypatch, capsys
):
    source_url = "https://upstream.example.test/image.png"
    cfg_path = write_config(
        tmp_path, min_free_space_mb=1, upstream={"allowed_hosts": ["upstream.example.test"]}
    )
    (tmp_path / "assets.yaml").write_text(
        yaml.safe_dump({
            "assets": [{
                "asset_id": "local-image",
                "source_url": source_url,
                "relative_path": "image.png",
                "permission_status": "approved",
                "permission_evidence": "Synthetic test fixture",
                "expected_sha256": None,
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.delenv("POOL_POC_ASSETS", raising=False)
    original = make_png(tmp_path / "original.png", 8, 8, seed=1)

    def stub_download(url, cfg, dest, max_size):
        assert url == source_url
        dest.write_bytes(original)
        return dest, "image/png", len(original)

    monkeypatch.setattr(cli, "download_to_file", stub_download)
    argv = ["--config", str(cfg_path)]
    assert cli.main([*argv, "sync", "local-image"]) == 0
    event = read_records(load_config(cfg_path))[-1]
    assert event["result"] == "published"
    assert event["hash_source"] == "local"
    assert event["sha256"] == sha256_of(original)
    assert cli.main([*argv, "verify", "local-image"]) == 0
    capsys.readouterr()

    target = tmp_path / "data" / "image.png"
    target.chmod(0o644)
    make_png(target, 8, 8, seed=2)

    assert cli.main([*argv, "verify", "local-image"]) == 1
    output = capsys.readouterr().out
    assert "MISMATCH" in output
    assert any(line.startswith("format") and line.endswith(": png") for line in output.splitlines())
