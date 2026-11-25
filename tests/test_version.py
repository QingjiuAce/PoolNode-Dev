"""Version identity: one source for package, CLI, and User-Agent."""
from __future__ import annotations

import pathlib
import re

import pytest

import pool
from pool import cli, downloader

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_version_is_a_pre_release_snapshot():
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev\d+)?", pool.__version__)


def test_cli_version_uses_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert f"pool-poc {pool.__version__}" in capsys.readouterr().out


def test_user_agent_uses_package_version():
    assert downloader.user_agent("sync") == f"pool-poc/{pool.__version__} (+sync)"
    assert downloader.user_agent("check-upstream") == (
        f"pool-poc/{pool.__version__} (+check-upstream)")


def test_no_legacy_hardcoded_version_in_package():
    for path in (ROOT / "pool").glob("*.py"):
        assert "pool-poc/0.1" not in path.read_text(encoding="utf-8"), path


def test_version_policy_documents_build_and_channels():
    text = (ROOT / "docs" / "VERSION_POLICY.md").read_text(encoding="utf-8")
    assert pool.__version__ in text
    for label in ("V0.2", "v0.4-beta", "v0.5.0-rc.1", "0.6.0.dev1"):
        assert label in text
