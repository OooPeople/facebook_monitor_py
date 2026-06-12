"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from facebook_monitor.updates.artifacts import update_artifact_policy_for_key
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_NAME
from facebook_monitor.updates.download import VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.platforms import detect_layout_policy
from tests.helpers.macos_bundle import MACHO_ARM64_BYTES
from tests.helpers.macos_bundle import write_path_to_zip_with_mode
from tests.helpers.macos_bundle import write_macos_app_bundle
from tests.helpers.macos_bundle import writestr_symlink


TEST_KEY_ID = "test-key"
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_REPOSITORY = "OooPeople/facebook_monitor_py"
TEST_VERSION = "0.1.0"


def trusted_public_keys() -> Mapping[str, str]:
    """回傳測試用 Ed25519 public key trust root。"""

    public_key = TEST_PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {TEST_KEY_ID: base64.b64encode(public_key).decode("ascii")}


def make_app_root(root: Path, *, exe_text: str) -> None:
    """建立最小 PyInstaller onedir 目錄。"""

    (root / "_internal" / "browser").mkdir(parents=True)
    (root / "_internal" / "assets").mkdir(parents=True)
    (root / "_internal" / "browser" / "chrome.exe").write_text("chrome", encoding="utf-8")
    (root / "_internal" / "python313.dll").write_text("runtime", encoding="utf-8")
    (root / "_internal" / "assets" / "facebook-monitor.ico").write_text(
        "icon",
        encoding="utf-8",
    )
    (root / "_internal" / "assets" / "facebook-monitor-tray.ico").write_text(
        "tray",
        encoding="utf-8",
    )
    (root / "facebook-monitor.exe").write_text(exe_text, encoding="utf-8")
    (root / "facebook-monitor-updater.exe").write_text("updater", encoding="utf-8")


def make_macos_app_root(root: Path, *, app_text: str) -> None:
    """建立最小 macOS arm64 onedir 目錄。"""

    browser = root / "browser" / "Chromium.app" / "Contents" / "MacOS"
    browser.mkdir(parents=True)
    browser_exe = browser / "Chromium"
    browser_exe.write_bytes(MACHO_ARM64_BYTES + b"chromium")
    browser_exe.chmod(0o755)
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "python").write_text("runtime", encoding="utf-8")
    app_entry = root / "facebook-monitor"
    updater_entry = root / "facebook-monitor-updater"
    app_entry.write_bytes(MACHO_ARM64_BYTES + app_text.encode("utf-8"))
    updater_entry.write_bytes(MACHO_ARM64_BYTES + b"updater")
    app_entry.chmod(0o755)
    updater_entry.chmod(0o755)
    make_macos_app_bundle(root)


def make_macos_chrome_for_testing_app_root(root: Path, *, app_text: str) -> None:
    """建立 Playwright Apple Silicon 目前常見的 macOS onedir fixture。"""

    browser = root / "browser" / "Google Chrome for Testing.app" / "Contents" / "MacOS"
    browser.mkdir(parents=True)
    browser_exe = browser / "Google Chrome for Testing"
    browser_exe.write_bytes(MACHO_ARM64_BYTES + b"chromium")
    browser_exe.chmod(0o755)
    (root / "_internal").mkdir(parents=True)
    (root / "_internal" / "python").write_text("runtime", encoding="utf-8")
    app_entry = root / "facebook-monitor"
    updater_entry = root / "facebook-monitor-updater"
    app_entry.write_bytes(MACHO_ARM64_BYTES + app_text.encode("utf-8"))
    updater_entry.write_bytes(MACHO_ARM64_BYTES + b"updater")
    app_entry.chmod(0o755)
    updater_entry.chmod(0o755)
    make_macos_app_bundle(root)


def make_macos_app_bundle(root: Path) -> None:
    """建立測試用 Finder/Dock `.app` launcher bundle。"""

    write_macos_app_bundle(root)


def make_update_zip(zip_path: Path, *, exe_text: str) -> str:
    """建立含單層 facebook-monitor 目錄的 update zip，回傳 SHA256。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_app_root(source_root, exe_text=exe_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(source_root.parent))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def make_macos_update_zip(zip_path: Path, *, app_text: str) -> str:
    """建立含單層 facebook-monitor 目錄的 macOS update zip。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text=app_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_symlink():
                writestr_symlink(
                    archive,
                    file_path.relative_to(source_root.parent).as_posix(),
                    file_path.readlink().as_posix(),
                )
            elif file_path.is_file():
                write_path_to_zip_with_mode(
                    archive,
                    file_path,
                    file_path.relative_to(source_root.parent),
                    _macos_zip_mode(file_path),
                )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def make_macos_chrome_for_testing_update_zip(zip_path: Path, *, app_text: str) -> str:
    """建立含 Google Chrome for Testing.app 的 macOS update zip。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_chrome_for_testing_app_root(source_root, app_text=app_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_symlink():
                writestr_symlink(
                    archive,
                    file_path.relative_to(source_root.parent).as_posix(),
                    file_path.readlink().as_posix(),
                )
            elif file_path.is_file():
                write_path_to_zip_with_mode(
                    archive,
                    file_path,
                    file_path.relative_to(source_root.parent),
                    _macos_zip_mode(file_path),
                )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def make_macos_root_level_update_zip(zip_path: Path, *, app_text: str) -> str:
    """建立 app files 直接位於 zip root 的 macOS update zip。"""

    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text=app_text)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_symlink():
                writestr_symlink(
                    archive,
                    file_path.relative_to(source_root).as_posix(),
                    file_path.readlink().as_posix(),
                )
            elif file_path.is_file():
                write_path_to_zip_with_mode(
                    archive,
                    file_path,
                    file_path.relative_to(source_root),
                    _macos_zip_mode(file_path),
                )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return digest


def _macos_zip_mode(path: Path) -> int:
    """測試用 macOS artifact zip mode。"""

    if path.name in {
        "facebook-monitor",
        "facebook-monitor-updater",
        "facebook-monitor-launcher",
        "Chromium",
        "Google Chrome for Testing",
    }:
        return 0o755
    return 0o644


def write_signed_manifest_for_pending(
    *,
    tmp_path: Path,
    zip_path: Path,
    digest: str,
    manifest_digest_override: str | None = None,
) -> tuple[str, Path, Path, str]:
    """建立與測試 app layout 對齊的 signed manifest metadata。"""

    app_base_dir = tmp_path / "app"
    layout_policy = detect_layout_policy(app_base_dir)
    artifact_policy = update_artifact_policy_for_key(layout_policy.platform_key)
    asset_name = artifact_policy.asset_name(TEST_VERSION)
    manifest_path = zip_path.with_name(f"facebook-monitor-{TEST_VERSION}-manifest.json")
    signature_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")
    zip_size = zip_path.stat().st_size if zip_path.exists() else 1
    manifest_payload = {
        "schema_version": 1,
        "version": TEST_VERSION,
        "repository": TEST_REPOSITORY,
        "key_id": TEST_KEY_ID,
        "assets": [
            {
                "name": asset_name,
                "platform": artifact_policy.platform_key,
                "sha256": digest,
                "size": zip_size,
            }
        ],
    }
    manifest_bytes = json.dumps(
        manifest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_bytes)
    signature_path.write_bytes(base64.b64encode(TEST_PRIVATE_KEY.sign(manifest_bytes)))
    manifest_digest = manifest_digest_override or hashlib.sha256(manifest_bytes).hexdigest()
    return asset_name, manifest_path, signature_path, manifest_digest


def pending_update(tmp_path: Path, *, zip_path: Path, digest: str) -> PendingUpdate:
    """建立測試用 pending update。"""

    asset_name, manifest_path, signature_path, manifest_digest = write_signed_manifest_for_pending(
        tmp_path=tmp_path,
        zip_path=zip_path,
        digest=digest,
    )
    sha256_path = zip_path.with_name(zip_path.name + ".sha256")
    sha256_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    marker_path = zip_path.parent / VERIFIED_DOWNLOAD_SET_MARKER_NAME
    marker_path.write_text(
        json.dumps(
            {
                "schema_version": VERIFIED_DOWNLOAD_SET_MARKER_SCHEMA_VERSION,
                "asset_name": zip_path.name,
                "asset_sha256": digest,
                "asset_size": zip_path.stat().st_size if zip_path.exists() else 1,
                "sha256_name": sha256_path.name,
                "sha256_sha256": hashlib.sha256(sha256_path.read_bytes()).hexdigest(),
                "manifest_name": manifest_path.name,
                "manifest_sha256": manifest_digest,
                "manifest_key_id": TEST_KEY_ID,
                "manifest_signature_name": signature_path.name,
                "manifest_signature_sha256": hashlib.sha256(
                    signature_path.read_bytes()
                ).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return PendingUpdate(
        schema_version=1,
        version=TEST_VERSION,
        repository=TEST_REPOSITORY,
        asset_name=asset_name,
        zip_path=zip_path,
        expected_sha256=digest,
        actual_sha256=digest,
        app_base_dir=tmp_path / "app",
        data_dir=tmp_path / "app" / "data",
        db_path=tmp_path / "app" / "data" / "app.db",
        profile_dir=tmp_path / "app" / "data" / "profiles" / "automation_default",
        logs_dir=tmp_path / "app" / "data" / "logs",
        runtime_dir=tmp_path / "app" / "data" / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_digest,
        manifest_key_id=TEST_KEY_ID,
    )


def pending_file_payload(pending: PendingUpdate) -> dict[str, object]:
    """將測試 pending update 轉成 JSON payload。"""

    return {
        "schema_version": pending.schema_version,
        "version": pending.version,
        "repository": pending.repository,
        "asset_name": pending.asset_name,
        "zip_path": str(pending.zip_path),
        "expected_sha256": pending.expected_sha256,
        "actual_sha256": pending.actual_sha256,
        "app_base_dir": str(pending.app_base_dir),
        "data_dir": str(pending.data_dir),
        "db_path": str(pending.db_path),
        "profile_dir": str(pending.profile_dir),
        "logs_dir": str(pending.logs_dir),
        "runtime_dir": str(pending.runtime_dir),
        "created_at": pending.created_at,
        "manifest_path": str(pending.manifest_path),
        "manifest_signature_path": str(pending.manifest_signature_path),
        "manifest_sha256": pending.manifest_sha256,
        "manifest_key_id": pending.manifest_key_id,
    }
