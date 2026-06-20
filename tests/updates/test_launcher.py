"""獨立 updater process 啟動測試。"""

from __future__ import annotations

import os
from pathlib import Path

from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.launcher import cleanup_old_temp_updaters
from facebook_monitor.updates.launcher import copy_updater_to_temp
from facebook_monitor.updates.launcher import find_bundled_updater
from facebook_monitor.updates.launcher import launch_restarted_app
from facebook_monitor.updates.launcher import launch_temp_updater
from facebook_monitor.updates.pending_update_models import PendingUpdate
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from tests.helpers.macos_bundle import assert_posix_executable_when_supported


def test_find_bundled_updater_returns_root_exe(tmp_path: Path) -> None:
    """frozen onedir 的 updater EXE 應放在 app root。"""

    updater = tmp_path / "app" / "facebook-monitor-updater.exe"
    updater.parent.mkdir()
    updater.write_text("exe", encoding="utf-8")

    assert find_bundled_updater(updater.parent) == updater.resolve()


def test_find_bundled_updater_returns_macos_root_binary(tmp_path: Path) -> None:
    """macOS onedir 的 updater binary 沒有 .exe 副檔名。"""

    updater = tmp_path / "app" / "facebook-monitor-updater"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")

    assert find_bundled_updater(updater.parent) == updater.resolve()


def test_copy_updater_to_temp_copies_outside_app_dir(tmp_path: Path) -> None:
    """updater 會被複製到 temp，避免鎖住 app base dir。"""

    source = tmp_path / "app" / "facebook-monitor-updater.exe"
    source.parent.mkdir()
    source.write_text("exe", encoding="utf-8")
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python313.dll").write_text("dll", encoding="utf-8")

    runtime_dir = tmp_path / "data" / "runtime"
    copied = copy_updater_to_temp(source, runtime_dir)

    assert copied.name == "facebook-monitor-updater.exe"
    assert copied.read_text(encoding="utf-8") == "exe"
    assert (copied.parent / "_internal" / "python313.dll").read_text(
        encoding="utf-8"
    ) == "dll"
    assert not copied.is_relative_to(source.parent)
    assert copied.is_relative_to(runtime_dir)


def test_copy_updater_to_temp_preserves_macos_updater_name(
    tmp_path: Path,
) -> None:
    """macOS temp updater copy 使用無副檔名 binary 並帶上 runtime 目錄。"""

    source = tmp_path / "app" / "facebook-monitor-updater"
    source.parent.mkdir()
    source.write_text("updater", encoding="utf-8")
    source.chmod(0o755)
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python").write_text("runtime", encoding="utf-8")

    copied = copy_updater_to_temp(source, tmp_path / "data" / "runtime")

    assert copied.name == "facebook-monitor-updater"
    assert copied.read_text(encoding="utf-8") == "updater"
    assert (copied.parent / "_internal" / "python").read_text(encoding="utf-8") == "runtime"
    assert_posix_executable_when_supported(copied)


def test_copy_updater_to_temp_uses_unique_directories(
    tmp_path: Path,
) -> None:
    """連續啟動 updater 時不可覆蓋前一次 temp runtime copy。"""

    source = tmp_path / "app" / "facebook-monitor-updater.exe"
    source.parent.mkdir()
    source.write_text("exe", encoding="utf-8")
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python313.dll").write_text("dll", encoding="utf-8")

    first = copy_updater_to_temp(source, tmp_path / "data" / "runtime")
    second = copy_updater_to_temp(source, tmp_path / "data" / "runtime")

    assert first.parent != second.parent
    assert first.is_file()
    assert second.is_file()


def test_copy_updater_to_temp_rejects_symlinked_runtime_temp_root(tmp_path: Path) -> None:
    """runtime temp updater root 若是 symlink，不可 follow 後寫入外部目錄。"""

    source = tmp_path / "app" / "facebook-monitor-updater"
    source.parent.mkdir()
    source.write_text("updater", encoding="utf-8")
    source.chmod(0o755)
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python").write_text("runtime", encoding="utf-8")
    runtime_dir = tmp_path / "data" / "runtime"
    temp_root = runtime_dir / "temp_updater"
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime_dir.mkdir(parents=True)
    try:
        temp_root.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    try:
        copy_updater_to_temp(source, runtime_dir)
    except ValueError as exc:
        assert str(exc) == "temp_updater_root_unsafe"
    else:
        raise AssertionError("expected symlinked temp updater root to fail")
    assert list(outside.iterdir()) == []


def test_copy_updater_to_temp_rejects_symlinked_runtime_dir(tmp_path: Path) -> None:
    """runtime dir 若是 symlink，不可 follow 後建立 temp updater。"""

    source = tmp_path / "app" / "facebook-monitor-updater"
    source.parent.mkdir()
    source.write_text("updater", encoding="utf-8")
    source.chmod(0o755)
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python").write_text("runtime", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (data_dir / "runtime").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    try:
        copy_updater_to_temp(source, data_dir / "runtime")
    except ValueError as exc:
        assert str(exc) == "temp_updater_root_unsafe"
    else:
        raise AssertionError("expected symlinked runtime dir to fail")
    assert list(outside.iterdir()) == []


def test_copy_updater_to_temp_rejects_symlinked_data_dir(tmp_path: Path) -> None:
    """runtime parent 若是 symlink，不可 follow 後寫入外部目錄。"""

    source = tmp_path / "app" / "facebook-monitor-updater"
    source.parent.mkdir()
    source.write_text("updater", encoding="utf-8")
    source.chmod(0o755)
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python").write_text("runtime", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "data").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        return

    try:
        copy_updater_to_temp(source, tmp_path / "data" / "runtime")
    except ValueError as exc:
        assert str(exc) == "temp_updater_root_unsafe"
    else:
        raise AssertionError("expected symlinked data dir to fail")
    assert list(outside.iterdir()) == []


def test_cleanup_old_temp_updaters_removes_stale_directories(tmp_path: Path) -> None:
    """temp updater root 會清掉過舊目錄並保留近期目錄。"""

    root = tmp_path / "updater"
    old_dir = root / "old"
    recent_dir = root / "recent"
    old_dir.mkdir(parents=True)
    recent_dir.mkdir()
    os.utime(old_dir, (0, 0))

    cleanup_old_temp_updaters(root, max_age_seconds=1)

    assert not old_dir.exists()
    assert recent_dir.exists()


def test_launch_temp_updater_builds_detached_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """啟動 updater 時使用 temp copy、pending_update 與 wait seconds。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.app_base_dir.mkdir(parents=True)
    (paths.app_base_dir / "facebook-monitor-updater.exe").write_text("exe", encoding="utf-8")
    (paths.app_base_dir / "_internal").mkdir()
    (paths.app_base_dir / "_internal" / "python313.dll").write_text("dll", encoding="utf-8")
    paths.runtime_dir.mkdir(parents=True)
    (paths.runtime_dir / "pending_update.json").write_text("{}", encoding="utf-8")
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 1234

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("facebook_monitor.updates.launcher.subprocess.Popen", fake_popen)

    result = launch_temp_updater(paths=paths, wait_seconds=45)

    assert result.status == "launched"
    assert result.pid == 1234
    command = launched["command"]
    assert isinstance(command, list)
    assert command[0].endswith("facebook-monitor-updater.exe")
    assert "--pending-update" in command
    assert str(paths.runtime_dir / "pending_update.json") in command
    assert "--wait-seconds" in command
    assert "45" in command
    assert "--restart" in command


def test_launch_restarted_app_preserves_runtime_path_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """重啟新版 app 時帶回 data/db/profile/logs 路徑。"""

    app_base_dir = tmp_path / "app"
    app_base_dir.mkdir()
    (app_base_dir / "facebook-monitor.exe").write_text("exe", encoding="utf-8")
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name="app.zip",
        zip_path=tmp_path / "app.zip",
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=tmp_path / "custom-data",
        db_path=tmp_path / "custom-data" / "custom.db",
        profile_dir=tmp_path / "custom-data" / "profiles" / "main",
        logs_dir=tmp_path / "custom-logs",
        runtime_dir=tmp_path / "custom-data" / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 5678

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("facebook_monitor.updates.launcher.subprocess.Popen", fake_popen)

    result = launch_restarted_app(pending)

    assert result.status == "launched"
    assert result.pid == 5678
    command = launched["command"]
    assert isinstance(command, list)
    assert command[0] == str(app_base_dir / "facebook-monitor.exe")
    assert "--data-dir" in command
    assert str(pending.data_dir) in command
    assert "--db-path" in command
    assert str(pending.db_path) in command
    assert "--profile-dir" in command
    assert str(pending.profile_dir) in command
    assert "--logs-dir" in command
    assert str(pending.logs_dir) in command


def test_launch_restarted_app_uses_macos_binary_and_detached_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """macOS restart path 使用 `.app` launcher 並用新 session detached 啟動。"""

    app_base_dir = tmp_path / "app"
    app_base_dir.mkdir()
    (app_base_dir / "facebook-monitor").write_text("app", encoding="utf-8")
    launcher = app_base_dir / MACOS_APP_BUNDLE_LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("launcher", encoding="utf-8")
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name="app.zip",
        zip_path=tmp_path / "app.zip",
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=tmp_path / "custom-data",
        db_path=tmp_path / "custom-data" / "custom.db",
        profile_dir=tmp_path / "custom-data" / "profiles" / "main",
        logs_dir=tmp_path / "custom-logs",
        runtime_dir=tmp_path / "custom-data" / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 9876

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("facebook_monitor.updates.launcher.sys.platform", "darwin")
    monkeypatch.setattr("facebook_monitor.updates.launcher.subprocess.Popen", fake_popen)

    result = launch_restarted_app(pending)

    assert result.status == "launched"
    assert result.pid == 9876
    command = launched["command"]
    assert isinstance(command, list)
    assert command[0] == str(launcher)
    kwargs = launched["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["start_new_session"] is True


def test_launch_restarted_app_reports_missing_macos_launcher(
    tmp_path: Path,
) -> None:
    """macOS update artifact 若缺 `.app` launcher，restart 不可退回 root binary。"""

    app_base_dir = tmp_path / "app"
    app_base_dir.mkdir()
    (app_base_dir / "facebook-monitor").write_text("app", encoding="utf-8")
    pending = PendingUpdate(
        schema_version=1,
        version="0.1.0",
        repository="OooPeople/facebook_monitor_py",
        asset_name="app.zip",
        zip_path=tmp_path / "app.zip",
        expected_sha256="a" * 64,
        actual_sha256="a" * 64,
        app_base_dir=app_base_dir,
        data_dir=tmp_path / "custom-data",
        db_path=tmp_path / "custom-data" / "custom.db",
        profile_dir=tmp_path / "custom-data" / "profiles" / "main",
        logs_dir=tmp_path / "custom-logs",
        runtime_dir=tmp_path / "custom-data" / "runtime",
        created_at="2026-05-17T00:00:00+00:00",
    )

    result = launch_restarted_app(pending)

    assert not result.launched
    assert result.status == "restart_entry_missing"
    assert result.message == str(app_base_dir / MACOS_APP_BUNDLE_LAUNCHER)
