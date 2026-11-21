"""资产白名单与授权判定。

授权是本项目的最高优先级约束：
- permission_status 只能是 pending / approved / denied；
- 只有 approved 且 permission_evidence 非空才允许同步；
- 不存在默认批准。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Any
from urllib.parse import urlparse

import yaml

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VALID_STATUSES = ("pending", "approved", "denied")


class PermissionError(Exception):
    """授权判定未通过。reason 用于日志与退出码区分。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class AssetConfigError(Exception):
    """白名单文件本身不合法（区别于单资产授权未通过）。"""


@dataclasses.dataclass(frozen=True)
class Asset:
    asset_id: str
    source_url: str
    relative_path: str
    permission_status: str
    permission_evidence: str
    attribution: str
    license: str
    expected_sha256: str | None
    revision: int


ASSET_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


def _parse_asset(raw: dict[str, Any], index: int) -> Asset:
    """将单条配置转成资产对象；实际同步另行判定授权与来源。"""
    where = f"assets[{index}]"
    asset_id = str(raw.get("asset_id", "")).strip()
    if not ASSET_ID_RE.match(asset_id):
        raise AssetConfigError(
            f"{where}: asset_id 必须匹配 ^[a-z0-9][a-z0-9-]{{1,63}}$: {asset_id!r}"
        )

    status = str(raw.get("permission_status", "")).strip().lower()
    if status not in VALID_STATUSES:
        raise AssetConfigError(
            f"{where} ({asset_id}): permission_status 只能是 {VALID_STATUSES}: {status!r}"
        )

    sha = raw.get("expected_sha256")
    if sha is not None:
        sha = str(sha).strip().lower()
        if not SHA256_RE.match(sha):
            raise AssetConfigError(
                f"{where} ({asset_id}): expected_sha256 必须是 64 位小写 hex 或 null: {sha!r}"
            )

    try:
        revision = int(raw.get("revision", 1))
    except (TypeError, ValueError) as e:
        raise AssetConfigError(f"{where} ({asset_id}): revision 必须是整数") from e
    if revision < 1:
        raise AssetConfigError(f"{where} ({asset_id}): revision 必须 >= 1")

    return Asset(
        asset_id=asset_id,
        source_url=str(raw.get("source_url", "")).strip(),
        relative_path=str(raw.get("relative_path", "")).strip(),
        permission_status=status,
        permission_evidence=str(raw.get("permission_evidence", "") or "").strip(),
        attribution=str(raw.get("attribution", "") or "").strip(),
        license=str(raw.get("license", "") or "").strip(),
        expected_sha256=sha,
        revision=revision,
    )


def load_assets(path: str | pathlib.Path) -> list[Asset]:
    """加载资产白名单文件。文件级错误立即抛出 AssetConfigError。"""
    p = pathlib.Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise AssetConfigError(f"资产白名单不存在: {p}") from e
    except yaml.YAMLError as e:
        raise AssetConfigError(f"资产白名单 YAML 解析失败: {p}: {e}") from e
    if not isinstance(raw, dict) or not isinstance(raw.get("assets"), list):
        raise AssetConfigError(f"资产白名单顶层必须含 assets 列表: {p}")

    assets: list[Asset] = []
    seen: set[str] = set()
    for i, item in enumerate(raw["assets"]):
        if not isinstance(item, dict):
            raise AssetConfigError(f"assets[{i}] 必须是映射")
        asset = _parse_asset(item, i)
        if asset.asset_id in seen:
            raise AssetConfigError(f"asset_id 重复: {asset.asset_id}")
        seen.add(asset.asset_id)
        assets.append(asset)
    return assets


def get_asset(assets: list[Asset], asset_id: str) -> Asset:
    """按资产 ID 查找白名单条目；找不到时立即拒绝。"""
    for asset in assets:
        if asset.asset_id == asset_id:
            return asset
    raise PermissionError(
        "NO_SUCH_ASSET", f"资产 {asset_id!r} 不在白名单中，拒绝任何操作"
    )


def check_permission(asset: Asset) -> None:
    """检查从白名单加载的状态及授权证据；不把待审、拒绝或无证据视为许可。"""
    # 待审与拒绝用不同原因码，保留授权决定的诊断信息。
    if asset.permission_status == "pending":
        raise PermissionError(
            "STATUS_PENDING",
            f"资产 {asset.asset_id} 状态为 pending：尚未获得明确第三方托管许可，拒绝同步",
        )
    if asset.permission_status == "denied":
        raise PermissionError(
            "STATUS_DENIED",
            f"资产 {asset.asset_id} 状态为 denied：已明确拒绝，拒绝同步",
        )
    if not asset.permission_evidence.strip():
        raise PermissionError(
            "NO_EVIDENCE",
            f"资产 {asset.asset_id} 为 approved 但 permission_evidence 为空：授权必须留证",
        )


def validate_source_url(asset: Asset, allowed_hosts: tuple[str, ...],
                        allow_loopback_http: bool = False) -> urlparse.ParseResult:
    """在下载前核对来源主机及资产路径。

    主机名必须精确命中白名单，不能仅凭域名后缀放行。生产环境要求
    HTTPS 且只接受默认端口；测试配置可对回环主机放宽 scheme 检查。
    拒绝非空的 query、fragment 和用户信息；路径后缀须与 relative_path 对应。
    """
    u = urlparse(asset.source_url)
    host = (u.hostname or "").lower()
    loopback_ok = allow_loopback_http and host in ("127.0.0.1", "localhost", "::1")

    if u.scheme != "https" and not loopback_ok:
        raise PermissionError(
            "BAD_SOURCE_URL", f"资产 {asset.asset_id}: 上游必须是 https://: {asset.source_url}"
        )
    if host not in allowed_hosts:
        raise PermissionError(
            "BAD_SOURCE_URL",
            f"资产 {asset.asset_id}: 上游主机 {host!r} 不在白名单 {list(allowed_hosts)} 内",
        )
    if u.scheme == "https" and u.port not in (None, 443):
        raise PermissionError(
            "BAD_SOURCE_URL", f"资产 {asset.asset_id}: https 不允许非标准端口: {u.port}"
        )
    if u.query or u.fragment or u.username or u.password:
        raise PermissionError(
            "BAD_SOURCE_URL", f"资产 {asset.asset_id}: 上游 URL 不允许携带 query/fragment/userinfo"
        )
    if not u.path or not u.path.startswith("/"):
        raise PermissionError("BAD_SOURCE_URL", f"资产 {asset.asset_id}: 上游 URL 路径非法")

    rel = asset.relative_path
    if not rel or not u.path.endswith("/" + rel.lstrip("/")):
        raise PermissionError(
            "BAD_SOURCE_URL",
            f"资产 {asset.asset_id}: URL 路径必须以 relative_path 结尾"
            f"（URL: {u.path!r}, relative_path: {rel!r}）",
        )
    return u
