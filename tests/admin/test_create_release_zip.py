"""Release zip builder tests。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from scripts.admin import create_release_zip
from tests.helpers.macos_bundle import MACHO_ARM64_BYTES
from tests.helpers.macos_bundle import assert_zip_member_executable
from tests.helpers.macos_bundle import write_macos_app_bundle


def test_create_release_zip_builds_windows_zip_and_sha(tmp_path: Path) -> None:
    """Windows release zip 會使用平台 asset name 並產生同名 SHA256。"""

    dist_dir = tmp_path / "dist"
    app_root = dist_dir / "facebook-monitor"
    for relative in (
        "facebook-monitor.exe",
        "facebook-monitor-updater.exe",
        "_internal/python313.dll",
        "_internal/browser/chrome.exe",
        "_internal/assets/facebook-monitor.ico",
        "_internal/assets/facebook-monitor-tray.ico",
    ):
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    result = create_release_zip.create_release_zip(
        platform_name="windows",
        version="0.1.0",
        dist_dir=dist_dir,
        force=True,
    )

    assert result.zip_path == dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    assert result.sha256_path == result.zip_path.with_name(result.zip_path.name + ".sha256")
    expected_digest = hashlib.sha256(result.zip_path.read_bytes()).hexdigest()
    assert result.sha256 == expected_digest
    assert result.sha256_path.read_text(encoding="ascii") == (
        f"{expected_digest}  {result.zip_path.name}\n"
    )
    with zipfile.ZipFile(result.zip_path) as archive:
        names = set(archive.namelist())
    assert "facebook-monitor/facebook-monitor.exe" in names
    assert "facebook-monitor/_internal/browser/chrome.exe" in names
    assert "facebook-monitor/README.md" not in names


def test_create_release_zip_rejects_existing_output_without_force(
    tmp_path: Path,
) -> None:
    """未指定 force 時不覆蓋既有 artifact。"""

    dist_dir = tmp_path / "dist"
    app_root = dist_dir / "facebook-monitor"
    for relative in (
        "facebook-monitor.exe",
        "facebook-monitor-updater.exe",
        "_internal/python313.dll",
        "_internal/browser/chrome.exe",
        "_internal/assets/facebook-monitor.ico",
        "_internal/assets/facebook-monitor-tray.ico",
    ):
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    output = dist_dir / "facebook-monitor-0.1.0-windows-portable.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("old", encoding="utf-8")

    with pytest.raises(ValueError, match="release_zip_output_exists"):
        create_release_zip.create_release_zip(
            platform_name="windows",
            version="0.1.0",
            dist_dir=dist_dir,
        )


def test_create_release_zip_rejects_sensitive_runtime_paths(tmp_path: Path) -> None:
    """release zip builder 不可把 data/profile/logs 類 runtime 資料包進去。"""

    dist_dir = tmp_path / "dist"
    app_root = dist_dir / "facebook-monitor"
    for relative in (
        "facebook-monitor.exe",
        "facebook-monitor-updater.exe",
        "_internal/python313.dll",
        "_internal/browser/chrome.exe",
        "_internal/assets/facebook-monitor.ico",
        "_internal/assets/facebook-monitor-tray.ico",
        "data/app.db",
    ):
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="release_zip_sensitive_path"):
        create_release_zip.create_release_zip(
            platform_name="windows",
            version="0.1.0",
            dist_dir=dist_dir,
            force=True,
        )


def test_create_release_zip_preserves_macos_executable_metadata(
    tmp_path: Path,
) -> None:
    """macOS release zip 會保留 app/updater/browser/.app launcher executable bit。"""

    dist_dir = tmp_path / "dist"
    app_root = dist_dir / "facebook-monitor"
    for relative, content in (
        ("facebook-monitor", MACHO_ARM64_BYTES + b"app"),
        ("facebook-monitor-updater", MACHO_ARM64_BYTES + b"updater"),
        (
            "browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"browser",
        ),
    ):
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755)
    write_macos_app_bundle(app_root, version="0.1.0")

    result = create_release_zip.create_release_zip(
        platform_name="macos-arm64",
        version="0.1.0",
        dist_dir=dist_dir,
        force=True,
    )

    assert result.zip_path == dist_dir / "facebook-monitor-0.1.0-macos-arm64-onedir.zip"
    with zipfile.ZipFile(result.zip_path) as archive:
        readme = archive.read("facebook-monitor/README.md").decode("utf-8")
        assert_zip_member_executable(archive, "facebook-monitor/facebook-monitor")
        assert_zip_member_executable(archive, "facebook-monitor/facebook-monitor-updater")
        assert_zip_member_executable(
            archive,
            "facebook-monitor/browser/Chromium.app/Contents/MacOS/Chromium",
        )
        assert_zip_member_executable(
            archive,
            (
                "facebook-monitor/Facebook Monitor.app/Contents/MacOS/"
                "facebook-monitor-launcher"
            ),
        )
    assert "xattr -dr com.apple.quarantine" in readme
    assert "Facebook Monitor.app" in readme


def test_create_release_zip_rejects_macos_non_arm64_executable(
    tmp_path: Path,
) -> None:
    """macOS arm64 artifact 壓縮前先拒絕非 arm64 executable。"""

    dist_dir = tmp_path / "dist"
    app_root = dist_dir / "facebook-monitor"
    for relative, content in (
        ("facebook-monitor", b"not-mach-o"),
        ("facebook-monitor-updater", MACHO_ARM64_BYTES + b"updater"),
        (
            "browser/Chromium.app/Contents/MacOS/Chromium",
            MACHO_ARM64_BYTES + b"browser",
        ),
    ):
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755)
    write_macos_app_bundle(app_root, version="0.1.0")

    with pytest.raises(ValueError, match="release_zip_macho_arm64_missing"):
        create_release_zip.create_release_zip(
            platform_name="macos-arm64",
            version="0.1.0",
            dist_dir=dist_dir,
            force=True,
        )
