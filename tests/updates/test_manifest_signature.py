"""Signed release manifest 驗證測試。"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import pytest

from facebook_monitor.updates.manifest import MAX_RELEASE_MANIFEST_BYTES
from facebook_monitor.updates.manifest import MAX_RELEASE_SIGNATURE_BYTES
from facebook_monitor.updates.manifest import verify_release_manifest


def signing_context() -> tuple[Ed25519PrivateKey, dict[str, str]]:
    """建立測試用 Ed25519 keypair 與 trusted key registry。"""

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, {"test-key": base64.b64encode(public_key).decode("ascii")}


def manifest_bytes(*, zip_bytes: bytes = b"zip", version: str = "0.1.0") -> bytes:
    """建立 canonical manifest JSON bytes。"""

    payload = {
        "schema_version": 1,
        "version": version,
        "repository": "OooPeople/facebook_monitor_py",
        "key_id": "test-key",
        "assets": [
            {
                "name": "facebook-monitor-0.1.0-windows-portable.zip",
                "platform": "windows",
                "sha256": hashlib.sha256(zip_bytes).hexdigest(),
                "size": len(zip_bytes),
            }
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest_bytes_from_payload(payload: object) -> bytes:
    """將 manifest payload 轉成測試用 canonical JSON bytes。"""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(private_key: Ed25519PrivateKey, payload: bytes) -> bytes:
    """回傳 base64 detached signature。"""

    return base64.b64encode(private_key.sign(payload))


def test_verify_release_manifest_accepts_valid_signature() -> None:
    """manifest 與 detached signature 都正確時回傳 asset hash metadata。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes(zip_bytes=b"zip")

    verified = verify_release_manifest(
        manifest_bytes=payload,
        signature_bytes=sign(private_key, payload),
        expected_version="0.1.0",
        expected_repository="OooPeople/facebook_monitor_py",
        expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
        expected_platform="windows",
        trusted_public_keys=trusted_keys,
    )

    assert verified.key_id == "test-key"
    assert verified.asset.sha256 == hashlib.sha256(b"zip").hexdigest()


def test_verify_release_manifest_rejects_wrong_signature() -> None:
    """簽章不是由 trusted key 產生時必須拒絕。"""

    private_key, trusted_keys = signing_context()
    other_private_key = Ed25519PrivateKey.generate()
    payload = manifest_bytes()

    try:
        verify_release_manifest(
            manifest_bytes=payload,
            signature_bytes=sign(other_private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )
    except ValueError as exc:
        assert str(exc) == "manifest_signature_invalid"
    else:
        raise AssertionError("expected wrong signature to fail")
    assert private_key is not other_private_key


def test_verify_release_manifest_rejects_tampered_manifest() -> None:
    """簽章後 manifest 被改動時必須拒絕。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes(zip_bytes=b"zip")
    tampered = manifest_bytes(zip_bytes=b"tampered")

    try:
        verify_release_manifest(
            manifest_bytes=tampered,
            signature_bytes=sign(private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )
    except ValueError as exc:
        assert str(exc) == "manifest_signature_invalid"
    else:
        raise AssertionError("expected tampered manifest to fail")


def test_verify_release_manifest_rejects_version_mismatch() -> None:
    """manifest version 必須與 release tag version 一致。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes(version="0.2.0")

    try:
        verify_release_manifest(
            manifest_bytes=payload,
            signature_bytes=sign(private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )
    except ValueError as exc:
        assert str(exc) == "manifest_version_mismatch"
    else:
        raise AssertionError("expected version mismatch to fail")


def test_verify_release_manifest_accepts_raw_detached_signature() -> None:
    """runtime 可接受測試與內部工具使用的 raw 64 bytes Ed25519 signature。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes(zip_bytes=b"zip")

    verified = verify_release_manifest(
        manifest_bytes=payload,
        signature_bytes=private_key.sign(payload),
        expected_version="0.1.0",
        expected_repository="OooPeople/facebook_monitor_py",
        expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
        expected_platform="windows",
        trusted_public_keys=trusted_keys,
    )

    assert verified.asset.size == len(b"zip")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ([], "manifest_json_invalid"),
        ({"schema_version": 999}, "manifest_schema_unsupported"),
        (
            {
                "schema_version": 1,
                "version": "0.1.0",
                "repository": "OooPeople/other",
                "key_id": "test-key",
                "assets": [],
            },
            "manifest_repository_mismatch",
        ),
        (
            {
                "schema_version": 1,
                "version": "0.1.0",
                "repository": "OooPeople/facebook_monitor_py",
                "key_id": "",
                "assets": [],
            },
            "manifest_key_missing",
        ),
    ],
)
def test_verify_release_manifest_rejects_invalid_headers(
    payload: object,
    reason: str,
) -> None:
    """manifest root/header 欄位錯誤時需保留穩定錯誤碼。"""

    private_key, trusted_keys = signing_context()
    manifest = manifest_bytes_from_payload(payload)

    with pytest.raises(ValueError, match=reason):
        verify_release_manifest(
            manifest_bytes=manifest,
            signature_bytes=sign(private_key, manifest),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )


def test_verify_release_manifest_rejects_untrusted_key() -> None:
    """manifest key_id 不在 trust root 時不可驗過。"""

    private_key, _trusted_keys = signing_context()
    payload = manifest_bytes_from_payload(
        {
            "schema_version": 1,
            "version": "0.1.0",
            "repository": "OooPeople/facebook_monitor_py",
            "key_id": "unknown-key",
            "assets": [],
        }
    )

    with pytest.raises(ValueError, match="manifest_key_untrusted"):
        verify_release_manifest(
            manifest_bytes=payload,
            signature_bytes=sign(private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys={},
        )


@pytest.mark.parametrize(
    ("assets", "reason"),
    [
        ("not-a-list", "manifest_assets_invalid"),
        ([], "manifest_asset_missing"),
        (
            [
                {
                    "name": "facebook-monitor-0.1.0-macos-arm64.zip",
                    "platform": "macos-arm64",
                    "sha256": "a" * 64,
                    "size": 1,
                }
            ],
            "manifest_asset_missing",
        ),
    ],
)
def test_verify_release_manifest_rejects_missing_or_invalid_assets(
    assets: object,
    reason: str,
) -> None:
    """manifest assets 容器錯誤或缺少本平台 asset 時需保留穩定 reason。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes_from_payload(
        {
            "schema_version": 1,
            "version": "0.1.0",
            "repository": "OooPeople/facebook_monitor_py",
            "key_id": "test-key",
            "assets": assets,
        }
    )

    with pytest.raises(ValueError, match=reason):
        verify_release_manifest(
            manifest_bytes=payload,
            signature_bytes=sign(private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )


@pytest.mark.parametrize(
    ("asset", "reason"),
    [
        (
            {
                "name": "facebook-monitor-0.1.0-windows-portable.zip",
                "platform": "macos-arm64",
                "sha256": "a" * 64,
                "size": 1,
            },
            "manifest_asset_platform_mismatch",
        ),
        (
            {
                "name": "facebook-monitor-0.1.0-windows-portable.zip",
                "platform": "windows",
                "sha256": "not-a-sha",
                "size": 1,
            },
            "manifest_asset_sha256_invalid",
        ),
        (
            {
                "name": "facebook-monitor-0.1.0-windows-portable.zip",
                "platform": "windows",
                "sha256": "a" * 64,
                "size": 0,
            },
            "manifest_asset_size_invalid",
        ),
    ],
)
def test_verify_release_manifest_rejects_invalid_asset_metadata(
    asset: dict[str, object],
    reason: str,
) -> None:
    """manifest asset metadata 不可和本次下載平台或 hash/size policy 漂移。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes_from_payload(
        {
            "schema_version": 1,
            "version": "0.1.0",
            "repository": "OooPeople/facebook_monitor_py",
            "key_id": "test-key",
            "assets": [asset],
        }
    )

    with pytest.raises(ValueError, match=reason):
        verify_release_manifest(
            manifest_bytes=payload,
            signature_bytes=sign(private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )


def test_verify_release_manifest_rejects_manifest_and_signature_size_limits() -> None:
    """manifest 與 detached signature 下載上限需在 verify 入口再次生效。"""

    private_key, trusted_keys = signing_context()
    payload = manifest_bytes()

    with pytest.raises(ValueError, match="manifest_too_large"):
        verify_release_manifest(
            manifest_bytes=b"{" + b" " * MAX_RELEASE_MANIFEST_BYTES + b"}",
            signature_bytes=sign(private_key, payload),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )
    with pytest.raises(ValueError, match="manifest_signature_too_large"):
        verify_release_manifest(
            manifest_bytes=payload,
            signature_bytes=b"x" * (MAX_RELEASE_SIGNATURE_BYTES + 1),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name="facebook-monitor-0.1.0-windows-portable.zip",
            expected_platform="windows",
            trusted_public_keys=trusted_keys,
        )
