"""獨立 updater 套用流程測試。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile


from facebook_monitor.updates.apply import apply_pending_update
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from tests.helpers.macos_bundle import assert_posix_executable_when_supported
from tests.helpers.macos_bundle import macos_app_plist
from tests.helpers.macos_bundle import MACHO_ARM64_BYTES
from tests.helpers.macos_bundle import write_macos_app_bundle
from tests.helpers.macos_bundle import write_path_to_zip_with_mode
from tests.helpers.macos_bundle import writestr_symlink
from tests.updates.apply_test_helpers import _macos_zip_mode
from tests.updates.apply_test_helpers import make_macos_app_root
from tests.updates.apply_test_helpers import make_macos_chrome_for_testing_app_root
from tests.updates.apply_test_helpers import make_macos_chrome_for_testing_update_zip
from tests.updates.apply_test_helpers import make_macos_root_level_update_zip
from tests.updates.apply_test_helpers import make_macos_update_zip
from tests.updates.apply_test_helpers import pending_update


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
        app_root / "Facebook Monitor.app" / "Contents" / "MacOS" / "facebook-monitor-launcher"
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
    legacy_launcher = b'#!/bin/sh\nexec ../facebook-monitor "$@"\n'
    write_macos_app_bundle(app_root, launcher_content=legacy_launcher)
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    digest = make_macos_update_zip(zip_path, app_text="new")

    result = apply_pending_update(pending_update(tmp_path, zip_path=zip_path, digest=digest))

    launcher = (
        app_root / "Facebook Monitor.app" / "Contents" / "MacOS" / "facebook-monitor-launcher"
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


def test_apply_pending_update_rejects_macos_bundle_identifier_mismatch(
    tmp_path: Path,
) -> None:
    """macOS `.app` bundle id 是通知權限 identity，staging 不可改掉。"""

    app_root = tmp_path / "app"
    make_macos_app_root(app_root, app_text="old")
    data_dir = app_root / "data"
    data_dir.mkdir()
    zip_path = data_dir / "updates" / "0.1.0" / "update.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    source_root = zip_path.parent / "new" / "facebook-monitor"
    make_macos_app_root(source_root, app_text="new")
    (source_root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(
        macos_app_plist(
            version="0.1.0",
            extra_values={"CFBundleIdentifier": "com.example.other"},
        )
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
    assert result.message == "staging_macos_bundle_identifier_mismatch"
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
