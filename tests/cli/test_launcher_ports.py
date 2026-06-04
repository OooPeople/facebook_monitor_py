"""Launcher single-instance behavior tests。"""

from __future__ import annotations


import pytest

from facebook_monitor import launcher
from facebook_monitor.runtime.logging_setup import reset_app_logging

def test_launcher_rejects_non_loopback_host(tmp_path, monkeypatch) -> None:
    """正式管理 UI 不允許綁到非 loopback host。"""

    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uvicorn should not run")),
    )

    with pytest.raises(SystemExit):
        launcher.main(["--data-dir", str(tmp_path / "data"), "--host", "0.0.0.0"])


def test_local_url_formats_ipv6_loopback_host() -> None:
    """IPv6 loopback URL 必須加上方括號，server.json 與開瀏覽器才可用。"""

    assert launcher._local_url("::1", 4818) == "http://[::1]:4818"


def test_launcher_auto_port_writes_effective_port(tmp_path, monkeypatch) -> None:
    """`--auto-port` 會把實際選到的 port 用於 uvicorn、server.json 與診斷。"""

    data_dir = tmp_path / "data"
    seen_ports: list[int] = []
    server_info_texts: list[str] = []

    monkeypatch.setattr(launcher, "_choose_available_port", lambda host: 54321)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        port = kwargs["port"]
        assert isinstance(port, int)
        seen_ports.append(port)
        server_info_texts.append((data_dir / "runtime" / "server.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)
    try:
        exit_code = launcher.main(["--data-dir", str(data_dir), "--auto-port"])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert seen_ports == [54321]
    assert '"port": 54321' in server_info_texts[0]
    assert "http://127.0.0.1:54321" in server_info_texts[0]
    startup_text = (data_dir / "logs" / "startup.log").read_text(encoding="utf-8")
    assert "Port: 54321" in startup_text
    assert "Auto port: true" in startup_text


def test_launcher_port_zero_uses_auto_port(tmp_path, monkeypatch) -> None:
    """`--port 0` 與 `--auto-port` 使用同一個 pre-resolve 行為。"""

    seen_ports: list[int] = []
    monkeypatch.setattr(launcher, "_choose_available_port", lambda host: 54322)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: seen_ports.append(kwargs["port"]),
    )

    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data"), "--port", "0"])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert seen_ports == [54322]


def test_launcher_no_auto_port_uses_fixed_default_port(tmp_path, monkeypatch) -> None:
    """`--no-auto-port` 會回到固定 4818 port，並仍可關閉自動開瀏覽器。"""

    seen_ports: list[int] = []
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: seen_ports.append(kwargs["port"]),
    )
    monkeypatch.setattr(launcher, "_port_is_available", lambda host, port: True)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)

    try:
        exit_code = launcher.main(
            ["--data-dir", str(tmp_path / "data"), "--no-auto-port", "--no-open-browser"]
        )
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert seen_ports == [4818]


def test_launcher_fixed_port_conflict_fails_before_uvicorn(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """固定 port 已被占用時，launcher 不寫 server.json 也不啟動 uvicorn。"""

    data_dir = tmp_path / "data"
    monkeypatch.setattr(launcher, "_port_is_available", lambda host, port: False)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uvicorn should not run")),
    )

    try:
        exit_code = launcher.main(["--data-dir", str(data_dir), "--port", "8765"])
    finally:
        reset_app_logging()

    assert exit_code == 2
    assert "已被使用" in capsys.readouterr().out
    assert not (data_dir / "runtime" / "server.json").exists()
    error_log_text = (data_dir / "logs" / "error.log").read_text(encoding="utf-8")
    assert "--auto-port" in error_log_text
