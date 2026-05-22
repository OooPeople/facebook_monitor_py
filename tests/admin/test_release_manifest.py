"""Release manifest admin scripts 測試。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from facebook_monitor.updates.manifest import verify_release_manifest
from scripts.admin.create_release_manifest import create_release_manifest
from scripts.admin.sign_release_manifest import sign_release_manifest


def test_create_and_sign_release_manifest_roundtrip(tmp_path: Path) -> None:
    """admin manifest / signature script 產物可被 runtime verifier 驗證。"""

    private_key = Ed25519PrivateKey.generate()
    private_key_b64 = base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    zip_path = tmp_path / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.write_bytes(b"zip")

    manifest_path = create_release_manifest(
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        key_id="test-key",
        asset_specs=[f"windows={zip_path}"],
        output=tmp_path / "facebook-monitor-0.1.0-manifest.json",
    )
    signature_path = sign_release_manifest(
        manifest_path=manifest_path,
        private_key_b64=private_key_b64,
    )

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    verified = verify_release_manifest(
        manifest_bytes=manifest_path.read_bytes(),
        signature_bytes=signature_path.read_bytes(),
        expected_version="0.1.0",
        expected_repository="OooPeople/facebook_monitor_py",
        expected_asset_name=zip_path.name,
        expected_platform="windows",
        trusted_public_keys={"test-key": public_key_b64},
    )

    assert payload["key_id"] == "test-key"
    assert verified.asset.name == zip_path.name
    assert verified.asset.size == len(b"zip")
