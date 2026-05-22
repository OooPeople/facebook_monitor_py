"""Signed release manifest 驗證測試。"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
