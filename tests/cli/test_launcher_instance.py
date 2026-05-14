"""Launcher single-instance behavior tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor import launcher
from facebook_monitor.runtime.instance_lock import AppInstanceLockError
from facebook_monitor.runtime.instance_lock import ServerInfo
from facebook_monitor.runtime.instance_lock import acquire_resource_identity_lock
from facebook_monitor.runtime.logging_setup import reset_app_logging


def test_launcher_opens_existing_healthy_instance(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """第二次啟動遇到健康既有 server 時不啟動新 uvicorn。"""

    info = ServerInfo(
        pid=123,
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765",
        started_at="2026-05-10T00:00:00+00:00",
    )
    opened_urls: list[str] = []

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise AppInstanceLockError("locked", server_info=info)

    monkeypatch.setattr(launcher, "acquire_app_instance_lock", raise_locked)
    monkeypatch.setattr(launcher, "_server_is_healthy", lambda url: True)
    monkeypatch.setattr(launcher, "_open_browser", opened_urls.append)
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uvicorn should not run")),
    )

    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data"), "--open-browser"])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert opened_urls == ["http://127.0.0.1:8765"]
    assert "已在執行" in capsys.readouterr().out


def test_launcher_default_existing_healthy_instance_does_not_open_new_tab(
    tmp_path,
    monkeypatch,
) -> None:
    """重複啟動時預設只回報既有 server，不重複開新瀏覽器分頁。"""

    info = ServerInfo(
        pid=123,
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765",
        started_at="2026-05-10T00:00:00+00:00",
    )
    opened_urls: list[str] = []

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise AppInstanceLockError("locked", server_info=info)

    monkeypatch.setattr(launcher, "acquire_app_instance_lock", raise_locked)
    monkeypatch.setattr(launcher, "_server_is_healthy", lambda url: True)
    monkeypatch.setattr(launcher, "_open_browser", opened_urls.append)

    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data")])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert opened_urls == []


def test_launcher_reports_locked_unhealthy_instance(tmp_path, monkeypatch) -> None:
    """lock 已被持有但 `/health` 無回應時，launcher 以非 0 結束。"""

    info = ServerInfo(
        pid=123,
        host="127.0.0.1",
        port=8765,
        url="http://127.0.0.1:8765",
        started_at="2026-05-10T00:00:00+00:00",
    )

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise AppInstanceLockError("locked", server_info=info)

    monkeypatch.setattr(launcher, "acquire_app_instance_lock", raise_locked)
    monkeypatch.setattr(launcher, "_server_is_healthy", lambda url: False)

    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data")])
    finally:
        reset_app_logging()

    assert exit_code == 2


def test_launcher_writes_startup_and_app_logs(tmp_path, monkeypatch, capsys) -> None:
    """正常 launcher 啟動會寫 startup.log 與 app.log。"""

    uvicorn_kwargs: list[dict[str, object]] = []

    def fake_uvicorn_run(*args: object, **kwargs: object) -> None:
        uvicorn_kwargs.append(kwargs)
        return None

    opened_urls: list[str] = []
    monkeypatch.setattr(launcher.uvicorn, "run", fake_uvicorn_run)
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

    monkeypatch.setattr(launcher.uvicorn, "run", fake_uvicorn_run)
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
        launcher.uvicorn,
        "run",
        lambda *args, **kwargs: seen_ports.append(kwargs["port"]),
    )

    try:
        exit_code = launcher.main(["--data-dir", str(tmp_path / "data")])
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert seen_ports == [54320]
    startup_text = (tmp_path / "data" / "logs" / "startup.log").read_text(
        encoding="utf-8"
    )
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

    monkeypatch.setattr(launcher.uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)
    try:
        exit_code = launcher.main(
            ["--data-dir", str(tmp_path / "data"), "--verbose-startup"]
        )
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

    monkeypatch.setattr(launcher.uvicorn, "run", fake_uvicorn_run)
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


def test_launcher_filters_windows_proactor_connection_lost_noise(monkeypatch) -> None:
    """Windows Proactor 關閉 pipe 時的 WinError 10022 不應噴到 console。"""

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    exception = OSError("bad argument")
    setattr(exception, "winerror", 10022)
    context = {
        "exception": exception,
        "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
    }

    assert launcher._is_windows_proactor_connection_lost_noise(context)


def test_launcher_event_loop_factory_import_path_is_valid() -> None:
    """uvicorn 設定中的 custom loop factory import path 必須可解析。"""

    config = launcher.uvicorn.Config(
        lambda scope, receive, send: None,
        loop="facebook_monitor.launcher:create_launcher_event_loop",
    )

    assert config.get_loop_factory() is launcher.create_launcher_event_loop


def test_launcher_keeps_unrelated_asyncio_exceptions(monkeypatch) -> None:
    """非目標 WinError 不能被 launcher exception handler 吃掉。"""

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    exception = OSError("other")
    setattr(exception, "winerror", 10054)

    assert not launcher._is_windows_proactor_connection_lost_noise(
        {
            "exception": exception,
            "handle": "<Handle _ProactorBasePipeTransport._call_connection_lost()>",
        }
    )


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
        server_info_texts.append(
            (data_dir / "runtime" / "server.json").read_text(encoding="utf-8")
        )

    monkeypatch.setattr(launcher.uvicorn, "run", fake_uvicorn_run)
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
        launcher.uvicorn,
        "run",
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
        launcher.uvicorn,
        "run",
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
        launcher.uvicorn,
        "run",
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


def test_launcher_rejects_shared_db_profile_across_data_dirs(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """不同 data-dir 只要共用實際 DB/profile，也不能同時啟動。"""

    shared_db = tmp_path / "shared" / "app.db"
    shared_profile = tmp_path / "shared" / "profile"
    shared_db.parent.mkdir()
    shared_profile.mkdir()
    second_data_dir = tmp_path / "second"
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uvicorn should not run")),
    )

    with acquire_resource_identity_lock(
        db_path=shared_db,
        profile_dir=shared_profile,
        owner="test-holder",
    ):
        try:
            exit_code = launcher.main(
                [
                    "--data-dir",
                    str(second_data_dir),
                    "--db-path",
                    str(shared_db),
                    "--unsafe-profile-dir",
                    str(shared_profile),
                ]
            )
        finally:
            reset_app_logging()

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "相同 SQLite DB" in output
    assert str(shared_db.resolve()) in output
    assert not (second_data_dir / "runtime" / "server.json").exists()


def test_launcher_rejects_shared_db_with_different_profile(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """同一 DB 即使搭配不同 profile，也不能由兩個 app instance 共用。"""

    shared_db = tmp_path / "shared" / "app.db"
    first_profile = tmp_path / "profiles" / "first"
    second_profile = tmp_path / "profiles" / "second"
    shared_db.parent.mkdir()
    first_profile.mkdir(parents=True)
    second_profile.mkdir()
    second_data_dir = tmp_path / "second-data"
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uvicorn should not run")),
    )

    with acquire_resource_identity_lock(
        db_path=shared_db,
        profile_dir=first_profile,
        owner="test-holder",
    ):
        try:
            exit_code = launcher.main(
                [
                    "--data-dir",
                    str(second_data_dir),
                    "--db-path",
                    str(shared_db),
                    "--unsafe-profile-dir",
                    str(second_profile),
                ]
            )
        finally:
            reset_app_logging()

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "相同 SQLite DB" in output
    assert str(shared_db.resolve()) in output


def test_launcher_rejects_shared_profile_with_different_db(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """同一 browser profile 即使搭配不同 DB，也不能由兩個 app instance 共用。"""

    first_db = tmp_path / "first" / "app.db"
    second_db = tmp_path / "second" / "app.db"
    shared_profile = tmp_path / "shared" / "profile"
    first_db.parent.mkdir()
    second_db.parent.mkdir()
    shared_profile.mkdir(parents=True)
    second_data_dir = tmp_path / "second-data"
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("uvicorn should not run")),
    )

    with acquire_resource_identity_lock(
        db_path=first_db,
        profile_dir=shared_profile,
        owner="test-holder",
    ):
        try:
            exit_code = launcher.main(
                [
                    "--data-dir",
                    str(second_data_dir),
                    "--db-path",
                    str(second_db),
                    "--unsafe-profile-dir",
                    str(shared_profile),
                ]
            )
        finally:
            reset_app_logging()

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "相同 browser profile" in output
    assert str(shared_profile.resolve()) in output


def test_launcher_allows_different_db_and_different_profile(tmp_path, monkeypatch) -> None:
    """DB 與 profile 都不同時，resource lock 不阻擋啟動。"""

    seen_ports: list[int] = []
    first_db = tmp_path / "first" / "app.db"
    second_db = tmp_path / "second" / "app.db"
    first_profile = tmp_path / "profiles" / "first"
    second_profile = tmp_path / "profiles" / "second"
    first_db.parent.mkdir()
    second_db.parent.mkdir()
    first_profile.mkdir(parents=True)
    second_profile.mkdir()

    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda *args, **kwargs: seen_ports.append(int(kwargs["port"])),
    )
    monkeypatch.setattr(launcher, "_open_browser", lambda url: None)

    with acquire_resource_identity_lock(
        db_path=first_db,
        profile_dir=first_profile,
        owner="test-holder",
    ):
        try:
            exit_code = launcher.main(
                [
                    "--data-dir",
                    str(tmp_path / "second-data"),
                    "--db-path",
                    str(second_db),
                    "--unsafe-profile-dir",
                    str(second_profile),
                    "--auto-port",
                ]
            )
        finally:
            reset_app_logging()

    assert exit_code == 0
    assert len(seen_ports) == 1
