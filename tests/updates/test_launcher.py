"""獨立 updater process 啟動測試。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.updates.launcher import copy_updater_to_temp
from facebook_monitor.updates.launcher import find_bundled_updater
from facebook_monitor.updates.launcher import launch_restarted_app
from facebook_monitor.updates.launcher import launch_temp_updater
from facebook_monitor.updates.handoff import PendingUpdate


def test_find_bundled_updater_returns_root_exe(tmp_path: Path) -> None:
    """frozen onedir 的 updater EXE 應放在 app root。"""

    updater = tmp_path / "app" / "facebook-monitor-updater.exe"
    updater.parent.mkdir()
    updater.write_text("exe", encoding="utf-8")

    assert find_bundled_updater(updater.parent) == updater.resolve()


def test_copy_updater_to_temp_copies_outside_app_dir(tmp_path: Path) -> None:
    """updater 會被複製到 temp，避免鎖住 app base dir。"""

    source = tmp_path / "app" / "facebook-monitor-updater.exe"
    source.parent.mkdir()
    source.write_text("exe", encoding="utf-8")
    (source.parent / "_internal").mkdir()
    (source.parent / "_internal" / "python313.dll").write_text("dll", encoding="utf-8")

    copied = copy_updater_to_temp(source, tmp_path / "data" / "runtime")

    assert copied.name == "facebook-monitor-updater.exe"
    assert copied.read_text(encoding="utf-8") == "exe"
    assert (copied.parent / "_internal" / "python313.dll").read_text(
        encoding="utf-8"
    ) == "dll"
    assert not copied.is_relative_to(source.parent)


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
