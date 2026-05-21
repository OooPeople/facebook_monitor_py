"""Runtime path resolver tests。"""

from __future__ import annotations

import sys

import pytest

from facebook_monitor.runtime.paths import DEFAULT_HOME_DATA_DIR_NAME
from facebook_monitor.runtime.paths import resolve_runtime_paths


def test_default_data_dir_uses_home_directory(monkeypatch, tmp_path) -> None:
    """未指定 data-dir 時，正式入口資料預設放在使用者 home 下。"""

    home_dir = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)

    paths = resolve_runtime_paths()

    assert paths.data_dir == (home_dir / DEFAULT_HOME_DATA_DIR_NAME).resolve()
    assert paths.db_path == paths.data_dir / "app.db"
    assert paths.logs_dir == paths.data_dir / "logs"
    assert paths.profile_dir == paths.data_dir / "profiles" / "automation_default"


def test_frozen_default_data_dir_uses_home_unless_portable(monkeypatch, tmp_path) -> None:
    """frozen 安裝型預設仍使用 home，只有 portable 才寫在 EXE 旁。"""

    home_dir = tmp_path / "home"
    executable = tmp_path / "Program Files" / "Facebook Monitor" / "facebook-monitor.exe"
    monkeypatch.setattr("pathlib.Path.home", lambda: home_dir)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    installed_paths = resolve_runtime_paths()
    portable_paths = resolve_runtime_paths(portable=True)

    assert installed_paths.data_dir == (home_dir / DEFAULT_HOME_DATA_DIR_NAME).resolve()
    assert portable_paths.data_dir == (executable.parent / "data").resolve()


def test_data_dir_drives_db_profile_and_logs(tmp_path) -> None:
    """`--data-dir` 會讓 Web UI 與 setup login 推導同一組 runtime paths。"""

    data_dir = tmp_path / "fb_monitor_data"

    paths = resolve_runtime_paths(data_dir=data_dir)

    assert paths.data_dir == data_dir.resolve()
    assert paths.db_path == data_dir.resolve() / "app.db"
    assert paths.profiles_dir == data_dir.resolve() / "profiles"
    assert paths.profile_dir == data_dir.resolve() / "profiles" / "automation_default"
    assert paths.logs_dir == data_dir.resolve() / "logs"
    assert paths.runtime_dir == data_dir.resolve() / "runtime"
    assert paths.exports_dir == data_dir.resolve() / "exports"
    assert paths.updates_dir == data_dir.resolve() / "updates"


def test_explicit_paths_override_data_dir(tmp_path) -> None:
    """明確指定 DB/profile/logs 時優先於 `--data-dir` 推導值。"""

    data_dir = tmp_path / "data"
    db_path = tmp_path / "custom" / "app.sqlite3"
    profile_dir = data_dir / "profiles" / "main"
    logs_dir = tmp_path / "custom_logs"

    paths = resolve_runtime_paths(
        data_dir=data_dir,
        db_path=db_path,
        profile_dir=profile_dir,
        logs_dir=logs_dir,
    )

    assert paths.db_path == db_path.resolve()
    assert paths.profile_dir == profile_dir.resolve()
    assert paths.profiles_dir == data_dir.resolve() / "profiles"
    assert paths.logs_dir == logs_dir.resolve()


def test_profile_dir_must_stay_under_profiles_dir(tmp_path) -> None:
    """正式 profile-dir 不可指向 runtime profiles 以外。"""

    try:
        resolve_runtime_paths(
            data_dir=tmp_path / "data",
            profile_dir=tmp_path / "external_profiles" / "main",
        )
    except ValueError as exc:
        assert "--profile-dir" in str(exc)
    else:
        raise AssertionError("expected external profile-dir to fail")


def test_default_profile_dir_rejects_existing_symlink_escape(tmp_path) -> None:
    """預設 profile path 也不得透過 symlink 逃出 `<data-dir>/profiles/`。"""

    data_dir = tmp_path / "data"
    external_profile = tmp_path / "external" / "automation_default"
    external_profile.mkdir(parents=True)
    profiles_dir = data_dir / "profiles"
    profiles_dir.mkdir(parents=True)
    link = profiles_dir / "automation_default"
    try:
        link.symlink_to(external_profile, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    try:
        resolve_runtime_paths(data_dir=data_dir)
    except ValueError as exc:
        assert "symlink" in str(exc) or "profile-dir" in str(exc)
    else:
        raise AssertionError("expected symlinked default profile-dir to fail")


def test_profiles_dir_rejects_existing_symlink_escape(tmp_path) -> None:
    """profiles root 本身若是 symlink/junction，正式 profile resolver 要拒絕。"""

    data_dir = tmp_path / "data"
    external_profiles = tmp_path / "external_profiles"
    external_profiles.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    link = data_dir / "profiles"
    try:
        link.symlink_to(external_profiles, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    try:
        resolve_runtime_paths(data_dir=data_dir)
    except ValueError as exc:
        assert "profiles" in str(exc) or "symlink" in str(exc)
    else:
        raise AssertionError("expected symlinked profiles dir to fail")


def test_unsafe_profile_dir_allows_external_debug_path(tmp_path) -> None:
    """debug-only unsafe flag 可支援測試用外部 profile。"""

    external_profile = tmp_path / "debug_profiles" / "main"

    paths = resolve_runtime_paths(
        data_dir=tmp_path / "data",
        profile_dir=external_profile,
        allow_external_profile_dir=True,
    )

    assert paths.profile_dir == external_profile.resolve()
    assert paths.profiles_dir == tmp_path.resolve() / "data" / "profiles"


def test_unsafe_profile_dir_rejects_common_browser_profile(tmp_path) -> None:
    """即使用 unsafe flag，也不能指向常見日常瀏覽器 profile。"""

    chrome_profile = tmp_path / "Google" / "Chrome" / "User Data" / "Default"

    try:
        resolve_runtime_paths(
            data_dir=tmp_path / "data",
            profile_dir=chrome_profile,
            allow_external_profile_dir=True,
        )
    except ValueError as exc:
        assert "--unsafe-profile-dir" in str(exc)
    else:
        raise AssertionError("expected common browser profile to fail")


def test_profile_name_must_be_folder_name(tmp_path) -> None:
    """profile name 只能是資料夾名稱，不能偷渡 path。"""

    try:
        resolve_runtime_paths(data_dir=tmp_path, profile_name="../daily")
    except ValueError as exc:
        assert "--profile-name" in str(exc)
    else:
        raise AssertionError("expected invalid profile name to fail")


def test_runtime_paths_can_resolve_bundled_web_resources(tmp_path, monkeypatch) -> None:
    """future frozen bundle 可從 `_MEIPASS` 找到 templates/static resource。"""

    package_root = tmp_path / "bundle" / "facebook_monitor"
    templates_dir = package_root / "webapp" / "templates"
    static_dir = package_root / "webapp" / "static"
    templates_dir.mkdir(parents=True)
    static_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)

    paths = resolve_runtime_paths(data_dir=tmp_path / "data")

    assert paths.templates_dir == templates_dir.resolve()
    assert paths.static_dir == static_dir.resolve()
