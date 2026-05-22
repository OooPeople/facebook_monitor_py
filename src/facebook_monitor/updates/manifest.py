"""Signed release manifest 驗證。

職責：驗證 GitHub Release manifest 的 Ed25519 detached signature，並確認
manifest 內容與目前 updater 準備下載的 repository、version、platform 與
asset 完全一致。
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from facebook_monitor.updates.artifacts import RELEASE_ASSET_PREFIX
from facebook_monitor.updates.trust import TRUSTED_RELEASE_PUBLIC_KEYS


RELEASE_MANIFEST_SCHEMA_VERSION = 1
MAX_RELEASE_MANIFEST_BYTES = 1024 * 1024
MAX_RELEASE_SIGNATURE_BYTES = 4096


@dataclass(frozen=True)
class ReleaseManifestAsset:
    """Signed manifest 中單一 release asset 的安全 metadata。"""

    name: str
    platform: str
    sha256: str
    size: int


@dataclass(frozen=True)
class VerifiedReleaseManifest:
    """已驗過簽章且與本次下載目標一致的 manifest 摘要。"""

    key_id: str
    manifest_sha256: str
    asset: ReleaseManifestAsset


def release_manifest_asset_name(version: str) -> str:
    """回傳 release manifest asset 名稱。"""

    return f"{RELEASE_ASSET_PREFIX}-{version}-manifest.json"


def release_manifest_signature_asset_name(version: str) -> str:
    """回傳 release manifest detached signature asset 名稱。"""

    return f"{release_manifest_asset_name(version)}.sig"


def verify_release_manifest(
    *,
    manifest_bytes: bytes,
    signature_bytes: bytes,
    expected_version: str,
    expected_repository: str,
    expected_asset_name: str,
    expected_platform: str,
    trusted_public_keys: Mapping[str, str] | None = None,
) -> VerifiedReleaseManifest:
    """驗證 signed manifest，並回傳目前平台 asset 的 hash metadata。"""

    if len(manifest_bytes) > MAX_RELEASE_MANIFEST_BYTES:
        raise ValueError("manifest_too_large")
    if len(signature_bytes) > MAX_RELEASE_SIGNATURE_BYTES:
        raise ValueError("manifest_signature_too_large")
    payload = _decode_manifest_json(manifest_bytes)
    _validate_manifest_header(
        payload,
        expected_version=expected_version,
        expected_repository=expected_repository,
    )
    key_id = str(payload.get("key_id", "")).strip()
    public_key_b64 = (trusted_public_keys or TRUSTED_RELEASE_PUBLIC_KEYS).get(key_id, "")
    if not public_key_b64:
        raise ValueError("manifest_key_untrusted")
    _verify_signature(
        public_key_b64=public_key_b64,
        signature_bytes=signature_bytes,
        manifest_bytes=manifest_bytes,
    )
    asset = _find_manifest_asset(
        payload,
        expected_asset_name=expected_asset_name,
        expected_platform=expected_platform,
    )
    return VerifiedReleaseManifest(
        key_id=key_id,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        asset=asset,
    )


def _decode_manifest_json(manifest_bytes: bytes) -> dict[str, Any]:
    """解析 manifest JSON，拒絕非物件 payload。"""

    try:
        payload = json.loads(manifest_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("manifest_json_invalid")
    return payload


def _validate_manifest_header(
    payload: dict[str, Any],
    *,
    expected_version: str,
    expected_repository: str,
) -> None:
    """確認 manifest 層級欄位與本次 release 目標一致。"""

    try:
        schema_version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("manifest_schema_unsupported") from exc
    if schema_version != RELEASE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("manifest_schema_unsupported")
    if str(payload.get("version", "")).strip() != expected_version:
        raise ValueError("manifest_version_mismatch")
    if str(payload.get("repository", "")).strip() != expected_repository:
        raise ValueError("manifest_repository_mismatch")
    if not str(payload.get("key_id", "")).strip():
        raise ValueError("manifest_key_missing")


def _verify_signature(
    *,
    public_key_b64: str,
    signature_bytes: bytes,
    manifest_bytes: bytes,
) -> None:
    """用 Ed25519 public key 驗 detached signature。"""

    try:
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)
        decoded_signature = _decode_signature(signature_bytes)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(decoded_signature, manifest_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("manifest_signature_invalid") from exc


def _decode_signature(signature_bytes: bytes) -> bytes:
    """接受 base64 text detached signature；測試中也允許 raw 64 bytes。"""

    if len(signature_bytes) == 64:
        return signature_bytes
    try:
        text = signature_bytes.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError("manifest_signature_invalid") from exc
    try:
        return base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise ValueError("manifest_signature_invalid") from exc


def _find_manifest_asset(
    payload: dict[str, Any],
    *,
    expected_asset_name: str,
    expected_platform: str,
) -> ReleaseManifestAsset:
    """取出本次平台要下載的 asset metadata。"""

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("manifest_assets_invalid")
    for item in assets:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).strip() != expected_asset_name:
            continue
        platform = str(item.get("platform", "")).strip()
        if platform != expected_platform:
            raise ValueError("manifest_asset_platform_mismatch")
        sha256 = str(item.get("sha256", "")).strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("manifest_asset_sha256_invalid")
        try:
            size = int(item.get("size", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("manifest_asset_size_invalid") from exc
        if size <= 0:
            raise ValueError("manifest_asset_size_invalid")
        return ReleaseManifestAsset(
            name=expected_asset_name,
            platform=platform,
            sha256=sha256,
            size=size,
        )
    raise ValueError("manifest_asset_missing")
