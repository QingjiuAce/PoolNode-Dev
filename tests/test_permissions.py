"""授权判定测试：pending/denied/无证据/未知资产/URL 白名单。"""

from __future__ import annotations

import sys
import pathlib

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pool.permissions import (
    Asset,
    AssetConfigError,
    PermissionError,
    check_permission,
    get_asset,
    load_assets,
    validate_source_url,
)


def _asset(**kw) -> Asset:
    defaults = dict(
        asset_id="a1",
        source_url="https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/x.png",
        relative_path="owner-picks/x.png",
        permission_status="approved",
        permission_evidence="站长确认",
        attribution="t", license="l",
        expected_sha256=None, revision=1,
    )
    defaults.update(kw)
    return Asset(**defaults)


ALLOWED = ("raw.githubusercontent.com", "objects.githubusercontent.com")


class TestCheckPermission:
    def test_approved_with_evidence_passes(self):
        check_permission(_asset())  # 不抛即通过

    def test_pending_rejected(self):
        with pytest.raises(PermissionError) as e:
            check_permission(_asset(permission_status="pending"))
        assert e.value.reason == "STATUS_PENDING"

    def test_denied_rejected(self):
        with pytest.raises(PermissionError) as e:
            check_permission(_asset(permission_status="denied"))
        assert e.value.reason == "STATUS_DENIED"

    def test_approved_without_evidence_rejected(self):
        with pytest.raises(PermissionError) as e:
            check_permission(_asset(permission_evidence=""))
        assert e.value.reason == "NO_EVIDENCE"

    def test_whitespace_only_evidence_rejected(self):
        with pytest.raises(PermissionError) as e:
            check_permission(_asset(permission_evidence="   "))
        assert e.value.reason == "NO_EVIDENCE"


class TestGetAsset:
    def test_found(self):
        assets = [_asset(asset_id="x1"), _asset(asset_id="x2")]
        assert get_asset(assets, "x2").asset_id == "x2"

    def test_unknown_asset_rejected(self):
        with pytest.raises(PermissionError) as e:
            get_asset([_asset()], "not-in-list")
        assert e.value.reason == "NO_SUCH_ASSET"


class TestSourceUrl:
    def test_valid_raw_url(self):
        validate_source_url(_asset(), ALLOWED)

    def test_http_rejected(self):
        with pytest.raises(PermissionError) as e:
            validate_source_url(_asset(source_url="http://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/x.png"), ALLOWED)
        assert e.value.reason == "BAD_SOURCE_URL"

    def test_disallowed_host_rejected(self):
        url = "https://evil.example.com/lmy414/ai-girl-stickers/main/owner-picks/x.png"
        with pytest.raises(PermissionError) as e:
            validate_source_url(_asset(source_url=url), ALLOWED)
        assert e.value.reason == "BAD_SOURCE_URL"

    def test_subdomain_spoof_rejected(self):
        url = "https://raw.githubusercontent.com.evil.com/lmy414/x.png"
        with pytest.raises(PermissionError) as e:
            validate_source_url(
                _asset(source_url=url, relative_path="lmy414/x.png"), ALLOWED)
        assert e.value.reason == "BAD_SOURCE_URL"

    def test_query_rejected(self):
        url = "https://raw.githubusercontent.com/lmy414/ai-girl-stickers/main/owner-picks/x.png?dl=1"
        with pytest.raises(PermissionError) as e:
            validate_source_url(_asset(source_url=url), ALLOWED)
        assert e.value.reason == "BAD_SOURCE_URL"

    def test_rel_path_suffix_mismatch_rejected(self):
        with pytest.raises(PermissionError) as e:
            validate_source_url(_asset(relative_path="owner-picks/other.png"), ALLOWED)
        assert e.value.reason == "BAD_SOURCE_URL"

    def test_loopback_http_only_with_flag(self):
        cfg_allowed = ("127.0.0.1",)
        a = _asset(source_url="http://127.0.0.1:8000/pool-tests/x.png",
                   relative_path="pool-tests/x.png")
        validate_source_url(a, cfg_allowed, allow_loopback_http=True)
        with pytest.raises(PermissionError):
            validate_source_url(a, cfg_allowed, allow_loopback_http=False)


class TestAssetsFile:
    def test_duplicate_id_rejected(self, tmp_path):
        p = tmp_path / "a.yaml"
        base = {"asset_id": "dup", "source_url": "https://raw.githubusercontent.com/x/y.png",
                "relative_path": "x/y.png", "permission_status": "pending",
                "permission_evidence": "", "attribution": "", "license": "",
                "expected_sha256": None, "revision": 1}
        p.write_text(yaml.safe_dump({"assets": [base, dict(base)]}), encoding="utf-8")
        with pytest.raises(AssetConfigError, match="重复"):
            load_assets(p)

    def test_bad_status_rejected(self, tmp_path):
        p = tmp_path / "a.yaml"
        a = {"asset_id": "xx", "permission_status": "auto-approve",
             "source_url": "https://raw.githubusercontent.com/x/y.png",
             "relative_path": "x/y.png"}
        p.write_text(yaml.safe_dump({"assets": [a]}), encoding="utf-8")
        with pytest.raises(AssetConfigError, match="permission_status"):
            load_assets(p)

    def test_bad_sha_rejected(self, tmp_path):
        p = tmp_path / "a.yaml"
        a = {"asset_id": "xx", "permission_status": "approved",
             "permission_evidence": "e", "expected_sha256": "deadbeef",
             "source_url": "https://raw.githubusercontent.com/x/y.png",
             "relative_path": "x/y.png"}
        p.write_text(yaml.safe_dump({"assets": [a]}), encoding="utf-8")
        with pytest.raises(AssetConfigError, match="expected_sha256"):
            load_assets(p)
