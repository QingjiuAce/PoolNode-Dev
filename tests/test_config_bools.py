"""Strict YAML booleans: quoted "false" must not become True."""
from __future__ import annotations

import pytest
import yaml

from conftest import write_config
from pool.config import ConfigError, load_config


def _load(tmp_path, **overrides):
    return load_config(write_config(tmp_path, **overrides))


def test_guard_defaults_true(tmp_path):
    cfg = _load(tmp_path)
    assert cfg.guard_system_disk is False  # fixture explicitly disables it
    cfg2 = load_config(_without_fixture_guard(tmp_path))
    assert cfg2.guard_system_disk is True


def _without_fixture_guard(tmp_path):
    path = write_config(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw.pop("guard_system_disk", None)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.mark.parametrize("value", ["false", "true", "", 0, 1, None, [], {}])
def test_guard_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ConfigError, match="guard_system_disk"):
        _load(tmp_path, guard_system_disk=value)


def test_guard_accepts_explicit_booleans(tmp_path):
    assert _load(tmp_path, guard_system_disk=True).guard_system_disk is True
    assert _load(tmp_path, guard_system_disk=False).guard_system_disk is False


def test_loopback_http_defaults_false(tmp_path):
    path = write_config(tmp_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["upstream"].pop("allow_loopback_http", None)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(path).upstream.allow_loopback_http is False


@pytest.mark.parametrize("value", ["false", "true", "", 0, 1, None, []])
def test_loopback_http_rejects_non_boolean(tmp_path, value):
    with pytest.raises(ConfigError, match="allow_loopback_http"):
        _load(tmp_path, upstream={
            "allowed_hosts": ["127.0.0.1"],
            "allow_loopback_http": value,
        })


def test_loopback_http_accepts_explicit_booleans(tmp_path):
    cfg = _load(tmp_path, upstream={
        "allowed_hosts": ["127.0.0.1"],
        "allow_loopback_http": True,
    })
    assert cfg.upstream.allow_loopback_http is True
