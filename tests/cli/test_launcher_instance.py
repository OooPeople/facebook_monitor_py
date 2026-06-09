"""Launcher single-instance behavior tests。"""

from __future__ import annotations

from pathlib import Path

import pytest

from facebook_monitor import launcher
from facebook_monitor.runtime.logging_setup import reset_app_logging
from facebook_monitor.runtime import windows_integration
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV
from facebook_monitor.updates.platforms import MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE

def test_formal_launcher_parser_does_not_expose_unsafe_profile_dir() -> None:
    """正式 launcher 不再暴露可指到外部 profile 的 debug-only 參數。"""

    parser = launcher.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--unsafe-profile-dir", "profile"])


def test_windows_tray_defaults_only_for_frozen_windows(monkeypatch) -> None:
    """source mode 預設不啟用 tray，避免影響 uv run 使用者。"""

    monkeypatch.setattr(windows_integration.sys, "platform", "win32")
    monkeypatch.delattr(windows_integration.sys, "frozen", raising=False)
    assert not windows_integration.resolve_windows_tray_decision(None).enabled

    monkeypatch.setattr(windows_integration.sys, "frozen", True, raising=False)
    assert windows_integration.resolve_windows_tray_decision(None).enabled
    assert not windows_integration.resolve_windows_tray_decision(False).enabled
    assert windows_integration.resolve_windows_tray_decision(True).enabled


def test_windows_notification_icon_prefers_pyinstaller_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Windows notification icon resolver 優先使用 onedir bundled tray icon。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor.exe"
    tray_icon = app_root / "_internal" / "assets" / "facebook-monitor-tray.ico"
    main_icon = app_root / "_internal" / "assets" / "facebook-monitor.ico"
    tray_icon.parent.mkdir(parents=True)
    executable.write_text("exe", encoding="utf-8")
    tray_icon.write_text("tray", encoding="utf-8")
    main_icon.write_text("main", encoding="utf-8")
    monkeypatch.setattr(windows_integration.sys, "executable", str(executable))
    monkeypatch.delattr(windows_integration.sys, "_MEIPASS", raising=False)

    assert windows_integration.find_windows_notification_icon() == tray_icon


def test_frozen_macos_root_binary_relaunches_via_app_launcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """舊 updater 直啟新版 root binary 時，launcher 會轉回 `.app` Dock 母程序。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    app_launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    app_launcher.parent.mkdir(parents=True)
    executable.write_text("app", encoding="utf-8")
    app_launcher.write_text("launcher", encoding="utf-8")
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        launched["command"] = command
        launched["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(executable))
    monkeypatch.delenv(MACOS_APP_BUNDLE_LAUNCHER_ENV, raising=False)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    exit_code = launcher.main(["--data-dir", str(tmp_path / "data"), "--no-open-browser"])

    assert exit_code == 0
    assert launched["command"] == [
        str(app_launcher),
        "--data-dir",
        str(tmp_path / "data"),
        "--no-open-browser",
    ]
    kwargs = launched["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(app_root)
    assert kwargs["start_new_session"] is True
    assert isinstance(kwargs["env"], dict)
    assert kwargs["env"][MACOS_APP_BUNDLE_LAUNCHER_ENV] == MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE


def test_frozen_macos_root_binary_guard_skips_app_relaunch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """`.app` native launcher 啟動的 child 不會再自我 relaunch 形成迴圈。"""

    app_root = tmp_path / "facebook-monitor"
    executable = app_root / "facebook-monitor"
    app_launcher = app_root / MACOS_APP_BUNDLE_LAUNCHER
    app_launcher.parent.mkdir(parents=True)
    executable.write_text("app", encoding="utf-8")
    app_launcher.write_text("launcher", encoding="utf-8")

    def fail_popen(*args: object, **kwargs: object) -> None:
        raise AssertionError("guarded child should not relaunch")

    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(executable))
    monkeypatch.setenv(
        MACOS_APP_BUNDLE_LAUNCHER_ENV,
        MACOS_APP_BUNDLE_LAUNCHER_ENV_VALUE,
    )
    monkeypatch.setattr(launcher.subprocess, "Popen", fail_popen)

    assert launcher._maybe_relaunch_via_macos_app([]) is None


def test_explicit_windows_tray_on_non_windows_falls_back_to_plain_server(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """非 Windows 平台明確傳 tray 也不應 import Windows-only tray module 崩潰。"""

    uvicorn_kwargs: list[dict[str, object]] = []

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        uvicorn_kwargs.append(kwargs)

    def fail_if_tray_runner_is_used(*args: object, **kwargs: object) -> None:
        raise AssertionError("tray runner should not be used on non-Windows")

    monkeypatch.setattr(windows_integration.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)
    monkeypatch.setattr(
        launcher,
        "run_uvicorn_with_windows_tray",
        fail_if_tray_runner_is_used,
    )

    try:
        exit_code = launcher.main(
            [
                "--data-dir",
                str(tmp_path / "data"),
                "--port",
                "8766",
                "--no-open-browser",
                "--windows-tray",
            ]
        )
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert uvicorn_kwargs[0]["port"] == 8766
    assert "only supported on Windows" in capsys.readouterr().out


def test_launcher_repairs_missing_standard_streams_for_gui_subsystem(
    monkeypatch,
) -> None:
    """Windows GUI EXE 沒有 console 時，uvicorn logging 仍需要可用 stream。"""

    class FakeDevNull:
        def write(self, value: str) -> int:
            return len(value)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return False

    opened_paths: list[str] = []

    def fake_open(path: str, *args: object, **kwargs: object) -> FakeDevNull:
        opened_paths.append(path)
        return FakeDevNull()

    monkeypatch.setattr(windows_integration.sys, "stdout", None)
    monkeypatch.setattr(windows_integration.sys, "stderr", None)
    monkeypatch.setattr("builtins.open", fake_open)

    windows_integration.ensure_standard_streams_for_gui_subsystem()

    assert opened_paths == [windows_integration.os.devnull, windows_integration.os.devnull]
    assert windows_integration.sys.stdout is not None
    assert windows_integration.sys.stderr is not None
    assert not windows_integration.sys.stderr.isatty()
