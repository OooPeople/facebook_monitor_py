"""Startup diagnostics and logging setup tests。"""

from __future__ import annotations

import logging

import facebook_monitor.runtime.startup_diagnostics as startup_diagnostics
from facebook_monitor.runtime.logging_setup import configure_app_logging
from facebook_monitor.runtime.logging_setup import LOG_BACKUP_COUNT
from facebook_monitor.runtime.logging_setup import LOG_MAX_BYTES
from facebook_monitor.runtime.logging_setup import reset_app_logging
from facebook_monitor.runtime.paths import resolve_runtime_paths
from facebook_monitor.runtime.startup_diagnostics import append_startup_log
from facebook_monitor.runtime.startup_diagnostics import build_startup_diagnostics


def test_configure_app_logging_writes_app_log(tmp_path) -> None:
    """Logging setup 會建立 app.log / error.log 並寫入 root logger 訊息。"""

    logs_dir = tmp_path / "logs"
    try:
        app_log_path = configure_app_logging(logs_dir, console=False)
        logging.getLogger("facebook_monitor.test").info("hello app log")
        logging.getLogger("facebook_monitor.test").error("hello error log")
    finally:
        reset_app_logging()

    assert app_log_path == logs_dir / "app.log"
    assert (logs_dir / "error.log").exists()
    assert LOG_MAX_BYTES == startup_diagnostics.STARTUP_LOG_MAX_BYTES
    assert LOG_BACKUP_COUNT == startup_diagnostics.STARTUP_LOG_BACKUP_COUNT
    assert "hello app log" in app_log_path.read_text(encoding="utf-8")
    assert "hello error log" in (logs_dir / "error.log").read_text(encoding="utf-8")


def test_configure_app_logging_keeps_info_out_of_default_console(
    tmp_path,
    capsys,
) -> None:
    """預設 console 只顯示 warning 以上，INFO 留在 app.log。"""

    logs_dir = tmp_path / "logs"
    try:
        configure_app_logging(logs_dir)
        logging.getLogger("facebook_monitor.test").info("hidden info")
        logging.getLogger("facebook_monitor.test").warning("visible warning")
    finally:
        reset_app_logging()

    captured = capsys.readouterr()
    assert "hidden info" not in captured.err
    assert "visible warning" in captured.err
    assert "hidden info" in (logs_dir / "app.log").read_text(encoding="utf-8")


def test_startup_diagnostics_append_startup_log(tmp_path) -> None:
    """Startup diagnostics 會追加寫入 logs/startup.log。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data")
    diagnostics = build_startup_diagnostics(
        paths=paths,
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765",
        open_browser=True,
        scheduler_interval_seconds=60,
        reset_runtime_data_on_startup=True,
        access_log=False,
    )

    startup_log_path = append_startup_log(paths.logs_dir, diagnostics)

    text = startup_log_path.read_text(encoding="utf-8")
    assert "Facebook Monitor" in text
    assert "URL: http://127.0.0.1:8765" in text
    assert "Auto port: false" in text
    assert "Asset version:" in text
    assert "Python version:" in text
    assert "Packaging mode: source" in text
    assert "Build date: unknown" in text
    assert "Git commit: unknown" in text
    assert f"Data dir: {paths.data_dir}" in text
    assert "Browser mode: playwright_chromium" in text
    assert "Scheduler tick seconds: 2" in text
    assert "Scheduler max concurrent scans: 4" in text
    assert "Reset targets on startup: true" in text
    assert "Resume active targets on startup: false" in text
    assert "Reset runtime data on startup: true" in text
    assert "Open browser: true" in text


def test_startup_diagnostics_rotates_startup_log(tmp_path, monkeypatch) -> None:
    """Startup diagnostics log 達到大小上限時會輪替，避免無限成長。"""

    monkeypatch.setattr(startup_diagnostics, "STARTUP_LOG_MAX_BYTES", 256)
    monkeypatch.setattr(startup_diagnostics, "STARTUP_LOG_BACKUP_COUNT", 2)
    paths = resolve_runtime_paths(data_dir=tmp_path / "data")
    diagnostics = build_startup_diagnostics(
        paths=paths,
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765",
        open_browser=False,
        scheduler_interval_seconds=60,
        reset_runtime_data_on_startup=True,
        access_log=False,
    )

    for _ in range(4):
        append_startup_log(paths.logs_dir, diagnostics)

    assert (paths.logs_dir / "startup.log").exists()
    assert (paths.logs_dir / "startup.log.1").exists()
    assert not (paths.logs_dir / "startup.log.3").exists()
