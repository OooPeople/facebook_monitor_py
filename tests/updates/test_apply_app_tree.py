"""Updater app tree validation helper tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_monitor.updates.apply_app_tree import find_staging_app_root
from facebook_monitor.updates.apply_app_tree import validate_staging_app_root
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_INFO_PLIST
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_ARM64_LAYOUT_POLICY
from tests.helpers.macos_bundle import macos_app_plist
from tests.updates.apply_test_helpers import make_app_root
from tests.updates.apply_test_helpers import make_macos_app_root


def test_find_staging_app_root_accepts_single_nested_app_dir(tmp_path: Path) -> None:
    """update zip 包一層 facebook-monitor 目錄時，staging root 應能被找到。"""

    staging_dir = tmp_path / "staging"
    app_root = staging_dir / "facebook-monitor"
    make_app_root(app_root, exe_text="new")

    assert find_staging_app_root(staging_dir) == app_root


def test_validate_staging_app_root_rejects_required_path_directory(
    tmp_path: Path,
) -> None:
    """required executable path 若變成 directory，不可進入替換流程。"""

    app_root = tmp_path / "facebook-monitor"
    make_app_root(app_root, exe_text="new")
    (app_root / "facebook-monitor.exe").unlink()
    (app_root / "facebook-monitor.exe").mkdir()

    with pytest.raises(ValueError, match="staging_required_file_not_file"):
        validate_staging_app_root(app_root)


def test_validate_staging_app_root_rejects_private_runtime_paths(
    tmp_path: Path,
) -> None:
    """update zip 不可夾帶 data/profiles/logs 等 runtime/private 資料。"""

    app_root = tmp_path / "facebook-monitor"
    make_app_root(app_root, exe_text="new")
    (app_root / "data" / "profiles").mkdir(parents=True)
    (app_root / "data" / "profiles" / "cookies.sqlite").write_text(
        "private",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="staging_private_data_path"):
        validate_staging_app_root(app_root)


def test_validate_staging_app_root_rejects_invalid_macos_info_plist(
    tmp_path: Path,
) -> None:
    """macOS `.app` Info.plist 無法解析時不可進入替換流程。"""

    app_root = tmp_path / "facebook-monitor"
    make_macos_app_root(app_root, app_text="new")
    (app_root / MACOS_APP_BUNDLE_INFO_PLIST).write_text("not plist", encoding="utf-8")

    with pytest.raises(ValueError, match="staging_macos_info_plist_invalid"):
        validate_staging_app_root(
            app_root,
            layout_policy=MACOS_ARM64_LAYOUT_POLICY,
            expected_version="0.1.0",
        )


def test_validate_staging_app_root_rejects_macos_bundle_executable_mismatch(
    tmp_path: Path,
) -> None:
    """macOS `.app` launcher executable metadata 不可漂移。"""

    app_root = tmp_path / "facebook-monitor"
    make_macos_app_root(app_root, app_text="new")
    (app_root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(
        macos_app_plist(
            extra_values={"CFBundleExecutable": Path(MACOS_APP_BUNDLE_LAUNCHER).name + "-other"}
        )
    )

    with pytest.raises(ValueError, match="staging_macos_bundle_executable_mismatch"):
        validate_staging_app_root(
            app_root,
            layout_policy=MACOS_ARM64_LAYOUT_POLICY,
            expected_version="0.1.0",
        )


def test_validate_staging_app_root_rejects_macos_short_version_mismatch(
    tmp_path: Path,
) -> None:
    """macOS `.app` short version 必須與 pending update version 一致。"""

    app_root = tmp_path / "facebook-monitor"
    make_macos_app_root(app_root, app_text="new")
    (app_root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(macos_app_plist(version="0.2.0"))

    with pytest.raises(ValueError, match="staging_macos_bundle_short_version_mismatch"):
        validate_staging_app_root(
            app_root,
            layout_policy=MACOS_ARM64_LAYOUT_POLICY,
            expected_version="0.1.0",
        )


def test_validate_staging_app_root_rejects_macos_bundle_version_mismatch(
    tmp_path: Path,
) -> None:
    """macOS `.app` bundle version 必須與 pending update version 一致。"""

    app_root = tmp_path / "facebook-monitor"
    make_macos_app_root(app_root, app_text="new")
    (app_root / MACOS_APP_BUNDLE_INFO_PLIST).write_bytes(
        macos_app_plist(
            version="0.1.0",
            extra_values={"CFBundleVersion": "0.2.0"},
        )
    )

    with pytest.raises(ValueError, match="staging_macos_bundle_version_mismatch"):
        validate_staging_app_root(
            app_root,
            layout_policy=MACOS_ARM64_LAYOUT_POLICY,
            expected_version="0.1.0",
        )
