"""Launcher single-instance behavior tests。"""

from __future__ import annotations

from pathlib import Path


from facebook_monitor import launcher
from facebook_monitor.runtime.logging_setup import reset_app_logging

def test_launcher_writes_startup_and_app_logs(tmp_path, monkeypatch, capsys) -> None:
    """正常 launcher 啟動會寫 startup.log 與 app.log。"""

    uvicorn_kwargs: list[dict[str, object]] = []

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        uvicorn_kwargs.append(kwargs)
        return None

    opened_urls: list[str] = []
    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)
    monkeypatch.setattr(launcher, "_open_browser", opened_urls.append)
    try:
        exit_code = launcher.main(
            ["--data-dir", str(tmp_path / "data"), "--port", "8765", "--no-open-browser"]
        )
    finally:
        reset_app_logging()

    logs_dir = tmp_path / "data" / "logs"
    assert exit_code == 0
    assert (logs_dir / "startup.log").exists()
    assert (logs_dir / "app.log").exists()
    assert uvicorn_kwargs[0]["loop"] == "facebook_monitor.launcher:create_launcher_event_loop"
    assert uvicorn_kwargs[0]["log_level"] == "warning"
    assert uvicorn_kwargs[0]["port"] == 8765
    assert opened_urls == []
    startup_text = (logs_dir / "startup.log").read_text(encoding="utf-8")
    assert "Facebook Monitor" in startup_text
    assert f"Data dir: {(tmp_path / 'data').resolve()}" in startup_text
    output = capsys.readouterr().out
    assert "Facebook Monitor" in output
    assert "Web UI：http://127.0.0.1:8765" in output
    assert "啟動診斷：" in output
    assert "按 CTRL+C 停止。" in output
    assert "Python version:" not in output
    assert "Resource lock paths:" not in output


def test_launcher_default_uses_home_data_default_port_and_opens_browser(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """不帶參數時使用 home data-dir、預設 port，並開啟瀏覽器。"""

    home_dir = tmp_path / "home"
    data_dir = home_dir / "facebook_monitor_data"
    uvicorn_kwargs: list[dict[str, object]] = []
    opened_urls: list[str] = []

    monkeypatch.setattr(Path, "home", lambda: home_dir)
    monkeypatch.setattr(launcher, "_port_is_available", lambda host, port: True)
    monkeypatch.setattr(
        launcher,
        "_choose_available_port",
        lambda host: (_ for _ in ()).throw(AssertionError("should not choose random port")),
    )
    monkeypatch.setattr(launcher, "_open_browser", opened_urls.append)

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        uvicorn_kwargs.append(kwargs)

    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)
    try:
        exit_code = launcher.main([])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert uvicorn_kwargs[0]["port"] == 4818
    assert opened_urls == ["http://127.0.0.1:4818"]
    assert (data_dir / "app.db").parent.exists()
    startup_text = (data_dir / "logs" / "startup.log").read_text(encoding="utf-8")
    assert f"Data dir: {data_dir.resolve()}" in startup_text
    assert "Auto port: false" in startup_text
    assert "Open browser: true" in startup_text
    output = capsys.readouterr().out
    assert "Web UI：http://127.0.0.1:4818" in output
    assert f"資料目錄：{data_dir.resolve()}" in output


def test_launcher_default_falls_back_to_auto_port_when_default_port_is_busy(
    tmp_path,
    monkeypatch,
) -> None:
    """不帶參數時，只有預設 4818 被占用才自動挑可用 port。"""

    seen_ports: list[int] = []
    monkeypatch.setattr(launcher, "_port_is_available", lambda host, port: False)
    monkeypatch.setattr(launcher, "_choose_available_port", lambda host: 54320)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: seen_ports.append(kwargs["port"]),
    )

    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data")])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert seen_ports == [54320]
    startup_text = (tmp_path / "data" / "logs" / "startup.log").read_text(encoding="utf-8")
    assert "Port: 54320" in startup_text
    assert "Auto port: true" in startup_text


def test_launcher_verbose_startup_prints_full_diagnostics(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """`--verbose-startup` 才把完整 startup diagnostics 印到 console。"""

    uvicorn_kwargs: list[dict[str, object]] = []

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        uvicorn_kwargs.append(kwargs)
        return None

    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)
    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data"), "--verbose-startup"])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert uvicorn_kwargs[0]["log_level"] == "info"
    output = capsys.readouterr().out
    assert "Python version:" in output
    assert "Resource lock paths:" in output
    assert "啟動診斷：" not in output


def test_launcher_access_log_keeps_uvicorn_info_level(tmp_path, monkeypatch) -> None:
    """`--access-log` 需要 uvicorn info level 才會真的印出 access log。"""

    uvicorn_kwargs: list[dict[str, object]] = []

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        uvicorn_kwargs.append(kwargs)

    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)
    try:
        exit_code = launcher.main(
            ["--data-dir", str(tmp_path / "data"), "--access-log", "--no-open-browser"]
        )
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert uvicorn_kwargs[0]["access_log"] is True
    assert uvicorn_kwargs[0]["log_level"] == "info"
