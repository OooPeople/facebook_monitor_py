"""Final release manifest admin script 測試。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from facebook_monitor.updates.checksum import calculate_sha256
from facebook_monitor.updates.checksum import render_sha256_sidecar
from facebook_monitor.updates.manifest import verify_release_manifest
from facebook_monitor.updates.trust import TRUSTED_RELEASE_PUBLIC_KEYS
from scripts.admin._release_build import DEFAULT_KEY_ID
from scripts.admin.finalize_release_manifest import finalize_release_manifest


def _release_key_pair() -> tuple[str, str]:
    """建立測試用 Ed25519 private/public key base64。"""

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
    return private_key_b64, public_key_b64


def test_default_release_key_id_is_trusted_by_runtime() -> None:
    """finalize 預設簽章 key 必須仍在 updater runtime trust root。"""

    assert DEFAULT_KEY_ID in TRUSTED_RELEASE_PUBLIC_KEYS


def _write_release_asset(dist_dir: Path, name: str, content: bytes = b"zip") -> Path:
    """寫出測試 release zip 與同名 `.sha256`。"""

    path = dist_dir / name
    path.write_bytes(content)
    digest = calculate_sha256(path)
    path.with_name(path.name + ".sha256").write_text(
        render_sha256_sidecar(digest, path.name),
        encoding="ascii",
    )
    return path


def test_finalize_release_manifest_uses_all_present_platform_assets(
    tmp_path: Path,
) -> None:
    """finalize 會把 dist 內目前版本的正式平台 asset 全部寫進同一份 manifest。"""

    private_key_b64, public_key_b64 = _release_key_pair()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    windows_zip = _write_release_asset(
        dist_dir,
        "facebook-monitor-0.1.0-windows-portable.zip",
    )
    macos_zip = _write_release_asset(
        dist_dir,
        "facebook-monitor-0.1.0-macos-arm64-onedir.zip",
        b"macos zip",
    )

    result = finalize_release_manifest(
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        dist_dir=dist_dir,
        key_id="test-key",
        private_key_b64=private_key_b64,
        validate_artifacts=False,
        force=True,
    )

    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assets = {asset["platform"]: asset for asset in payload["assets"]}
    assert result.platforms == ("windows", "macos-arm64")
    assert assets["windows"]["name"] == windows_zip.name
    assert assets["macos-arm64"]["name"] == macos_zip.name
    for platform, zip_path in (
        ("windows", windows_zip),
        ("macos-arm64", macos_zip),
    ):
        verified = verify_release_manifest(
            manifest_bytes=result.manifest_path.read_bytes(),
            signature_bytes=result.signature_path.read_bytes(),
            expected_version="0.1.0",
            expected_repository="OooPeople/facebook_monitor_py",
            expected_asset_name=zip_path.name,
            expected_platform=platform,
            trusted_public_keys={"test-key": public_key_b64},
        )
        assert verified.asset.sha256 == calculate_sha256(zip_path)


def test_finalize_release_manifest_accepts_single_platform_release(
    tmp_path: Path,
) -> None:
    """單平台 release 也可產生只含該平台 asset 的 manifest。"""

    private_key_b64, _ = _release_key_pair()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    _write_release_asset(dist_dir, "facebook-monitor-0.1.0-windows-portable.zip")

    result = finalize_release_manifest(
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        dist_dir=dist_dir,
        key_id="test-key",
        private_key_b64=private_key_b64,
        validate_artifacts=False,
        force=True,
    )

    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.platforms == ("windows",)
    assert [asset["platform"] for asset in payload["assets"]] == ["windows"]


def test_finalize_release_manifest_rejects_unexpected_release_zip(
    tmp_path: Path,
) -> None:
    """finalize 不應寬鬆簽署 dist 內未知或舊版 release zip。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    _write_release_asset(dist_dir, "facebook-monitor-0.1.0-windows-portable.zip")
    (dist_dir / "facebook-monitor-0.1.0-extra.zip").write_bytes(b"unexpected")

    with pytest.raises(ValueError, match="release_manifest_unexpected_artifact"):
        finalize_release_manifest(
            version="0.1.0",
            dist_dir=dist_dir,
            private_key_b64=_release_key_pair()[0],
            validate_artifacts=False,
            force=True,
        )


def test_finalize_release_manifest_rejects_sha256_mismatch(tmp_path: Path) -> None:
    """finalize 前必須先確認 zip 與 sidecar SHA256 完全一致。"""

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    zip_path = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    zip_path.write_bytes(b"zip")
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{'0' * 64}  {zip_path.name}",
        encoding="ascii",
    )

    with pytest.raises(ValueError, match="release_manifest_sha256_mismatch"):
        finalize_release_manifest(
            version="0.1.0",
            dist_dir=dist_dir,
            private_key_b64=_release_key_pair()[0],
            validate_artifacts=False,
            force=True,
        )
