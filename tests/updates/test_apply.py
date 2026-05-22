"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.updates import apply as updater_apply
from facebook_monitor.updates.artifacts import update_artifact_policy_for_key
from facebook_monitor.updates.apply import apply_loaded_pending_update_file
from facebook_monitor.updates.apply import apply_pending_update_file
from facebook_monitor.updates.apply import apply_pending_update
from facebook_monitor.updates.apply import _backup_folder_name
from facebook_monitor.updates.apply import _cleanup_old_backup_dirs
from facebook_monitor.updates.apply import _prepare_empty_dir
from facebook_monitor.updates.apply import safe_extract_zip
from facebook_monitor.updates.apply import UpdaterApplyResult
from facebook_monitor.updates.handoff import PendingUpdate
from facebook_monitor.updates.platforms import detect_layout_policy
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from tests.helpers.macos_bundle import assert_posix_executable_when_supported
from tests.helpers.macos_bundle import assert_zip_member_executable
from tests.helpers.macos_bundle import macos_app_plist
from tests.helpers.macos_bundle import MACHO_ARM64_BYTES
from tests.helpers.macos_bundle import write_path_to_zip_with_mode
from tests.helpers.macos_bundle import write_macos_app_bundle
from tests.helpers.macos_bundle import writestr_symlink


TEST_KEY_ID = "test-key"
TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_REPOSITORY = "OooPeople/facebook_monitor_py"
TEST_VERSION = "0.1.0"


@pytest.fixture(autouse=True)
def trust_test_release_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """apply 階段使用測試 release public key 驗 signed manifest。"""

    monkeypatch.setattr(
        updater_apply,
        "TRUSTED_RELEASE_PUBLIC_KEYS",
        trusted_public_keys(),
    )


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

    asset_name, manifest_path, signature_path, manifest_digest = (
        write_signed_manifest_for_pending(
            tmp_path=tmp_path,
            zip_path=zip_path,
            digest=digest,
        )
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


def test_apply_pending_update_replaces_app_files_but_preserves_data(tmp_path: Path) -> None:
    """updater 會替換 app files，並保留 portable data dir。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "new"
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    assert result.backup_dir is not None
    assert (result.backup_dir / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_supports_macos_arm64_onedir_layout(
    tmp_path: Path,
) -> None:
    """platform layout policy 允許 macOS onedir 替換 app files 並保留 data。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"new")
    assert_posix_executable_when_supported(app_root / "facebook-monitor")
    assert_posix_executable_when_supported(app_root / "facebook-monitor-updater")
    assert_posix_executable_when_supported(
        app_root
        / "Facebook Monitor.app"
        / "Contents"
        / "MacOS"
        / "facebook-monitor-launcher"
    )
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    assert result.backup_dir is not None
    assert (result.backup_dir / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_supports_macos_root_level_zip_layout(
    tmp_path: Path,
) -> None:
    """macOS executable bit 驗證需支援 app files 直接位於 zip root 的布局。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_root_level_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"new")
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"


def test_apply_pending_update_rejects_macos_zip_without_executable_bit_metadata(
    tmp_path: Path,
) -> None:
    """Windows 也要用 zip metadata 擋下缺 executable bit 的 macOS 更新包。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text="new")
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_symlink():
                writestr_symlink(
                    archive,
                    file_path.relative_to(source_root.parent).as_posix(),
                    file_path.readlink().as_posix(),
                )
            elif file_path.is_file():
                mode = 0o644 if file_path.name == "facebook-monitor" else _macos_zip_mode(file_path)
                write_path_to_zip_with_mode(
                    archive,
                    file_path,
                    file_path.relative_to(source_root.parent),
                    mode,
                )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "staging_executable_bit_missing:facebook-monitor/facebook-monitor"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_rejects_macos_browser_without_executable_bit_metadata(
    tmp_path: Path,
) -> None:
    """Windows 也要檢查 macOS browser executable 的 zip metadata。"""

    app_root = tmp_path / "app"
    make_macos_chrome_for_testing_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_chrome_for_testing_app_root(source_root, app_text="new")
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in source_root.rglob("*"):
            if file_path.is_symlink():
                writestr_symlink(
                    archive,
                    file_path.relative_to(source_root.parent).as_posix(),
                    file_path.readlink().as_posix(),
                )
            elif file_path.is_file():
                mode = (
                    0o644
                    if file_path.name == "Google Chrome for Testing"
                    else _macos_zip_mode(file_path)
                )
                write_path_to_zip_with_mode(
                    archive,
                    file_path,
                    file_path.relative_to(source_root.parent),
                    mode,
                )
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == (
        "staging_executable_bit_missing:"
        "facebook-monitor/browser/Google Chrome for Testing.app/Contents/MacOS/"
        "Google Chrome for Testing"
    )
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_preserves_safe_macos_symlinks(tmp_path: Path) -> None:
    """macOS PyInstaller onedir 內安全的相對 symlink 應被保留。"""

    if os.name == "nt":
        return
    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text="new")
    (source_root / "python-link").symlink_to("_internal/python")
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

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    link = app_root / "python-link"
    assert result.status == "applied"
    assert link.is_symlink()
    assert link.readlink() == Path("_internal/python")


def test_apply_pending_update_replaces_legacy_macos_shell_launcher(
    tmp_path: Path,
) -> None:
    """更新套用時會把舊 shell `.app` launcher 覆蓋成新版 native launcher。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    legacy_launcher = b"#!/bin/sh\nexec ../facebook-monitor \"$@\"\n"
    write_macos_app_bundle(app_root, launcher_content=legacy_launcher)
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    launcher = (
        app_root
        / "Facebook Monitor.app"
        / "Contents"
        / "MacOS"
        / "facebook-monitor-launcher"
    )
    assert result.status == "applied"
    assert result.applied
    assert launcher.read_bytes() == MACHO_ARM64_BYTES
    assert result.backup_dir is not None
    assert (
        result.backup_dir
        / "Facebook Monitor.app"
        / "Contents"
        / "MacOS"
        / "facebook-monitor-launcher"
    ).read_bytes() == legacy_launcher


def test_apply_pending_update_supports_macos_chrome_for_testing_layout(
    tmp_path: Path,
) -> None:
    """macOS updater layout 接受 Playwright 的 Google Chrome for Testing.app。"""

    app_root = tmp_path / "app"
    make_macos_chrome_for_testing_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_chrome_for_testing_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "applied"
    assert result.applied
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"new")
    assert_posix_executable_when_supported(app_root / "facebook-monitor")
    assert_posix_executable_when_supported(app_root / "facebook-monitor-updater")
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    browser_exe = (
        app_root
        / "browser"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )
    assert browser_exe.read_bytes().endswith(b"chromium")
    assert_posix_executable_when_supported(browser_exe)


def test_apply_pending_update_refuses_when_app_lock_is_held(tmp_path: Path) -> None:
    """主程式仍持有 app lock 時，updater 不替換檔案。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)

    with acquire_app_instance_lock(pending.runtime_dir, "test"):
        result = apply_pending_update(pending)

    assert result.status == "app_running"
    assert not result.applied
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_refuses_symlinked_staging_dir(tmp_path: Path) -> None:
    """staging dir 若被 symlink 到外部，updater 不可 follow 後刪除 target。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_text("do not delete", encoding="utf-8")
    staging_dir = data_dir / "runtime" / "update_staging" / "0.1.0"
    staging_dir.parent.mkdir(parents=True)
    try:
        staging_dir.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "update_work_dir_unsafe"
    assert keep.read_text(encoding="utf-8") == "do not delete"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_refuses_symlinked_staging_parent(tmp_path: Path) -> None:
    """staging parent 若是 symlink，updater 不可 follow 後寫入外部目錄。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    outside = tmp_path / "outside"
    outside.mkdir()
    staging_parent = data_dir / "runtime" / "update_staging"
    staging_parent.parent.mkdir(parents=True)
    try:
        staging_parent.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "update_work_dir_unsafe"
    assert list(outside.iterdir()) == []
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_refuses_symlinked_backup_parent(tmp_path: Path) -> None:
    """backup parent 若是 symlink，updater 不可 follow 後寫入外部目錄。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    outside = tmp_path / "outside"
    outside.mkdir()
    backup_parent = data_dir / "runtime" / "update_backups"
    backup_parent.parent.mkdir(parents=True)
    try:
        backup_parent.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "update_work_dir_unsafe"
    assert list(outside.iterdir()) == []
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_rejects_current_symlink_to_data(tmp_path: Path) -> None:
    """目前 app root 內的 symlink 不可指向 preserved data/profile 路徑。"""

    if os.name == "nt":
        return
    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    (data_dir / "profiles").mkdir(parents=True)
    (app_root / "profile-link").symlink_to("data/profiles")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message.startswith("app_path_unsafe:")
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_rejects_macos_plist_hidden_string(
    tmp_path: Path,
) -> None:
    """LSUIElement 用字串表示 true 時也不可通過。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text="new")
    (source_root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(
        macos_app_plist(version="0.1.0", extra_values={"LSUIElement": "1"})
    )
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

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "staging_macos_bundle_hidden_from_dock"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_rejects_macos_background_only_integer(
    tmp_path: Path,
) -> None:
    """LSBackgroundOnly 用非零 integer 表示 true 時也不可通過。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text="new")
    (source_root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(
        macos_app_plist(version="0.1.0", extra_values={"LSBackgroundOnly": 2})
    )
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

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "staging_macos_bundle_hidden_from_dock"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")


def test_apply_pending_update_restores_backup_when_replace_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """replace 中途失敗時應還原舊 app files 並保留 data。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    (data_dir / "app.db").write_text("user data", encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")
    original_copy_path = updater_apply._copy_path

    def flaky_copy_path(source: Path, destination: Path, *, source_root: Path) -> None:
        if source.name == "facebook-monitor" and source.read_bytes().endswith(b"new"):
            raise OSError("copy failed")
        original_copy_path(source, destination, source_root=source_root)

    monkeypatch.setattr(updater_apply, "_copy_path", flaky_copy_path)

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "copy failed"
    assert (app_root / "facebook-monitor").read_bytes().endswith(b"old")
    assert (data_dir / "app.db").read_text(encoding="utf-8") == "user data"
    launcher = (
        app_root
        / "Facebook Monitor.app"
        / "Contents"
        / "MacOS"
        / "facebook-monitor-launcher"
    )
    assert launcher.read_bytes() == MACHO_ARM64_BYTES


def test_apply_pending_update_file_writes_result_log(tmp_path: Path) -> None:
    """updater CLI path 會把套用結果寫進 updater log。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )
    log_path = data_dir / "logs" / "updater.log"

    result = apply_pending_update_file(pending_path, log_path=log_path)

    assert result.status == "applied"
    assert "status=applied applied=true message=updated" in log_path.read_text(
        encoding="utf-8"
    )
    assert not pending_path.exists()
    assert not zip_path.exists()
    assert not zip_path.with_name(zip_path.name + ".sha256").exists()


def test_apply_pending_update_file_removes_verified_sha256_asset(tmp_path: Path) -> None:
    """成功套用後會移除本次下載的 zip 與 `.sha256`，避免更新檔長期殘留。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    zip_path.with_name(zip_path.name + ".sha256").write_text(
        f"{digest}  {zip_path.name}\n",
        encoding="utf-8",
    )
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )

    result = apply_pending_update_file(pending_path)

    assert result.applied
    assert not zip_path.exists()
    assert not zip_path.with_name(zip_path.name + ".sha256").exists()
    assert not pending_path.exists()


def test_apply_pending_update_file_prunes_old_backups(tmp_path: Path) -> None:
    """成功套用後只保留最近 backup，避免舊備份無限制累積。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    data_dir = app_root / "data"
    runtime_dir = data_dir / "runtime"
    backup_root = runtime_dir / "update_backups"
    runtime_dir.mkdir(parents=True)
    for index in range(5):
        old_backup = backup_root / (
            f"0.1.0-20260517T00000{index}000000Z-deadbee{index}"
        )
        old_backup.mkdir(parents=True)
        (old_backup / "marker.txt").write_text(str(index), encoding="utf-8")
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=digest)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(pending_file_payload(pending)),
        encoding="utf-8",
    )

    result = apply_pending_update_file(pending_path)

    assert result.applied
    assert result.backup_dir is not None
    retained = {path.name for path in backup_root.iterdir() if path.is_dir()}
    assert len(retained) == 3
    assert result.backup_dir.name in retained
    assert "0.1.0-20260517T000004000000Z-deadbee4" in retained
    assert "0.1.0-20260517T000003000000Z-deadbee3" in retained


def test_backup_folder_name_uses_unique_suffix() -> None:
    """同一秒同版本建立 backup 時，資料夾名稱仍不應碰撞。"""

    first = _backup_folder_name("0.1.0")
    second = _backup_folder_name("0.1.0")

    assert first != second


def test_cleanup_old_backup_dirs_skips_unknown_backup_names(tmp_path: Path) -> None:
    """不符合 updater 命名格式的資料夾不可被自動刪除。"""

    backup_root = tmp_path / "runtime" / "update_backups"
    backup_root.mkdir(parents=True)
    unknown = backup_root / "manual-backup"
    unknown.mkdir()
    old = backup_root / "0.1.0-20260517T000001000000Z-deadbee1"
    new = backup_root / "0.1.0-20260517T000002000000Z-deadbee2"
    old.mkdir()
    new.mkdir()

    warnings = _cleanup_old_backup_dirs(backup_root, keep_count=1, preserve=None)

    assert unknown.exists()
    assert new.exists()
    assert not old.exists()
    assert any("backup_unknown" in warning for warning in warnings)


def test_cleanup_old_backup_dirs_rejects_root_that_escapes_runtime(
    tmp_path: Path,
) -> None:
    """backup root 若被 symlink/junction 導到 runtime 外，不可清理外部資料。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("external data", encoding="utf-8")
    backup_root = runtime_dir / "update_backups"
    try:
        backup_root.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    warnings = _cleanup_old_backup_dirs(backup_root, keep_count=1, preserve=None)

    assert warnings
    assert (outside / "keep.txt").exists()


def test_prepare_empty_dir_rejects_path_that_resolves_to_work_root(
    tmp_path: Path,
) -> None:
    """update staging/backup 目錄不可用 `..` 解析成 runtime root 後被清空。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    marker = runtime_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    try:
        _prepare_empty_dir(runtime_dir / "update_staging" / "..", work_root=runtime_dir)
    except ValueError as exc:
        assert str(exc) == "update_work_dir_unsafe"
    else:
        raise AssertionError("expected work root path to fail")
    assert marker.read_text(encoding="utf-8") == "keep"


def test_apply_loaded_pending_update_file_logs_cleanup_warnings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """cleanup 失敗會寫入 updater log，但不改變成功套用結果。"""

    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    pending = pending_update(tmp_path, zip_path=zip_path, digest="a" * 64)
    pending_path = tmp_path / "app" / "data" / "runtime" / "pending_update.json"
    log_path = tmp_path / "app" / "data" / "logs" / "updater.log"

    def fake_apply_pending_update(*args, **kwargs) -> UpdaterApplyResult:
        return UpdaterApplyResult(status="applied", applied=True, message="updated")

    def fake_cleanup_applied_update(*args, **kwargs) -> tuple[str, ...]:
        return ("pending:EACCES",)

    monkeypatch.setattr(
        "facebook_monitor.updates.apply.apply_pending_update",
        fake_apply_pending_update,
    )
    monkeypatch.setattr(
        "facebook_monitor.updates.apply._cleanup_applied_update",
        fake_cleanup_applied_update,
    )

    result = apply_loaded_pending_update_file(pending, pending_path, log_path=log_path)

    assert result.applied
    log_text = log_path.read_text(encoding="utf-8")
    assert "status=applied applied=true message=updated" in log_text
    assert "cleanup_warning=pending:EACCES" in log_text


def test_apply_pending_update_rejects_hash_changed_after_handoff(tmp_path: Path) -> None:
    """handoff 後 zip 被替換時，updater 會重算 SHA256 並拒絕套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    zip_path.write_bytes(b"changed")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "pending_zip_sha256_mismatch"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_rejects_manifest_changed_after_handoff(
    tmp_path: Path,
) -> None:
    """handoff 後 manifest 被替換時，updater 會重算 SHA256 並拒絕套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_update_zip(zip_path, exe_text="new")
    manifest_path = zip_path.with_name("facebook-monitor-0.1.0-manifest.json")
    manifest_path.write_text("original", encoding="utf-8")
    manifest_digest = hashlib.sha256(b"original").hexdigest()
    manifest_path.write_text("changed", encoding="utf-8")
    signature_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")
    signature_path.write_text("sig", encoding="utf-8")

    pending = replace(
        pending_update(tmp_path, zip_path=zip_path, digest=digest),
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_digest,
        manifest_key_id="test-key",
    )
    result = apply_pending_update(pending)

    assert result.status == "failed"
    assert result.message == "pending_manifest_sha256_mismatch"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_apply_pending_update_rejects_self_consistent_manifest_without_valid_signature(
    tmp_path: Path,
) -> None:
    """zip、pending 與 manifest 被一起改寫但 signature 不符時不可套用。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "app" / "data" / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    original_digest = make_update_zip(zip_path, exe_text="new")
    pending = pending_update(tmp_path, zip_path=zip_path, digest=original_digest)
    assert pending.manifest_signature_path is not None
    original_signature = pending.manifest_signature_path.read_bytes()
    shutil.rmtree(zip_path.parent / "new")
    tampered_digest = make_update_zip(zip_path, exe_text="evil")
    _, manifest_path, signature_path, manifest_digest = write_signed_manifest_for_pending(
        tmp_path=tmp_path,
        zip_path=zip_path,
        digest=tampered_digest,
    )
    signature_path.write_bytes(original_signature)
    tampered_pending = replace(
        pending,
        expected_sha256=tampered_digest,
        actual_sha256=tampered_digest,
        manifest_path=manifest_path,
        manifest_signature_path=signature_path,
        manifest_sha256=manifest_digest,
    )

    result = apply_pending_update(tampered_pending)

    assert result.status == "failed"
    assert result.message == "manifest_signature_invalid"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    """zip 不能含有會逃出 staging dir 的 member path。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../evil.txt", "bad")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_member_path_unsafe"
    else:
        raise AssertionError("expected unsafe zip member to fail")


def test_safe_extract_zip_preserves_executable_bit(tmp_path: Path) -> None:
    """macOS updater 解壓 staging 時必須保留 executable bit。"""

    source = tmp_path / "source" / "facebook-monitor"
    source.parent.mkdir()
    source.write_text("app", encoding="utf-8")
    source.chmod(0o755)
    zip_path = tmp_path / "app.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        write_path_to_zip_with_mode(archive, source, "facebook-monitor/facebook-monitor", 0o755)
        assert_zip_member_executable(archive, "facebook-monitor/facebook-monitor")

    safe_extract_zip(zip_path, tmp_path / "staging")

    extracted = tmp_path / "staging" / "facebook-monitor" / "facebook-monitor"
    assert extracted.read_text(encoding="utf-8") == "app"
    assert_posix_executable_when_supported(extracted)


def test_safe_extract_zip_preserves_safe_symlink(tmp_path: Path) -> None:
    """POSIX zip symlink 若留在 staging tree 內，updater 會保留 symlink。"""

    if os.name == "nt":
        return
    zip_path = tmp_path / "app.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("facebook-monitor/_internal/lib.dylib", "lib")
        writestr_symlink(
            archive,
            "facebook-monitor/lib.dylib",
            "_internal/lib.dylib",
        )

    safe_extract_zip(zip_path, tmp_path / "staging")

    link = tmp_path / "staging" / "facebook-monitor" / "lib.dylib"
    assert link.is_symlink()
    assert link.readlink() == Path("_internal/lib.dylib")


def test_safe_extract_zip_rejects_escaping_symlink(tmp_path: Path) -> None:
    """zip symlink target 不可逃出 staging tree。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/link", "../../outside")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_symlink_target_unsafe"
    else:
        raise AssertionError("expected escaping symlink to fail")


def test_safe_extract_zip_rejects_backslash_symlink_target(tmp_path: Path) -> None:
    """zip symlink target 必須是實際會被 POSIX symlink 使用的 slash path。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/link", "_internal\\lib.dylib")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_symlink_target_unsafe"
    else:
        raise AssertionError("expected backslash symlink target to fail")


def test_safe_extract_zip_rejects_symlink_to_private_data(tmp_path: Path) -> None:
    """zip symlink 不可指向更新後會變成 preserved data/profile 的路徑。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/profile-link", "data/profiles")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_symlink_target_unsafe"
    else:
        raise AssertionError("expected symlink to private data to fail")


def test_safe_extract_zip_rejects_member_under_symlink(tmp_path: Path) -> None:
    """zip 不可先建立 symlink directory 再把 member 寫入該路徑底下。"""

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        writestr_symlink(archive, "facebook-monitor/link", "_internal")
        archive.writestr("facebook-monitor/link/file.txt", "bad")

    try:
        safe_extract_zip(zip_path, tmp_path / "staging")
    except ValueError as exc:
        assert str(exc) == "zip_member_path_unsafe"
    else:
        raise AssertionError("expected member under symlink to fail")


def test_apply_pending_update_rejects_zip_outside_updates_dir(tmp_path: Path) -> None:
    """pending update 不能指向 data updates 目錄外的 zip。"""

    app_root = tmp_path / "app"
    make_app_root(app_root, exe_text="old")
    zip_path = tmp_path / "update.zip"
    digest = make_update_zip(zip_path, exe_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    assert result.status == "failed"
    assert result.message == "pending_update_zip_outside_updates_dir"
    assert (app_root / "facebook-monitor.exe").read_text(encoding="utf-8") == "old"


def test_safe_extract_zip_rejects_oversized_archive(tmp_path: Path) -> None:
    """解壓前會檢查展開後大小，避免異常 zip 耗盡磁碟。"""

    zip_path = tmp_path / "big.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("large.bin", "12345")

    try:
        safe_extract_zip(
            zip_path,
            tmp_path / "staging",
            max_uncompressed_bytes=4,
        )
    except ValueError as exc:
        assert str(exc) == "zip_uncompressed_too_large"
    else:
        raise AssertionError("expected oversized zip to fail")
