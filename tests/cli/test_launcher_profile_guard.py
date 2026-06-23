"""Launcher single-instance behavior tests。"""

from __future__ import annotations

from pathlib import Path


from facebook_monitor import launcher
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.runtime.instance_lock import acquire_resource_identity_lock
from facebook_monitor.runtime.logging_setup import reset_app_logging

def test_launcher_runs_guided_login_when_profile_status_needs_login(
    tmp_path,
    monkeypatch,
) -> None:
    """needs_login 狀態下，launcher 先完成引導登入再啟動 Web UI。"""

    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    profile_dir = data_dir / "profiles" / "automation_default"
    with SqliteApplicationContext(db_path) as app:
        app.repositories.app_settings.mark_profile_needs_login(
            reason="login_required",
            source="resident_main",
        )
    login_profile_dirs: list[Path] = []
    uvicorn_ports: list[int] = []

    def fake_guided_login(options: launcher.GuidedLoginOptions) -> bool:
        login_profile_dirs.append(options.profile_dir)
        return True

    monkeypatch.setattr(launcher, "run_guided_facebook_login", fake_guided_login)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: uvicorn_ports.append(int(kwargs["port"])),
    )

    try:
        exit_code = launcher.main(
            ["--data-dir", str(data_dir), "--no-open-browser", "--port", "8765"]
        )
    finally:
        reset_app_logging()

    with SqliteApplicationContext(db_path) as app:
        status = app.repositories.app_settings.get_profile_session_status()
    assert exit_code == 0
    assert login_profile_dirs == [profile_dir]
    assert uvicorn_ports == [8765]
    assert status.state == ProfileSessionState.OK


def test_launcher_runs_guided_login_when_profile_has_no_cookie_session(
    tmp_path,
    monkeypatch,
) -> None:
    """沒有 Facebook cookie session 的全新 profile，launcher 先引導登入。"""

    data_dir = tmp_path / "data"
    profile_dir = data_dir / "profiles" / "automation_default"
    login_profile_dirs: list[Path] = []
    uvicorn_ports: list[int] = []

    monkeypatch.setattr(launcher, "profile_has_facebook_session_cookies", lambda _path: False)

    def fake_guided_login(options: launcher.GuidedLoginOptions) -> bool:
        login_profile_dirs.append(options.profile_dir)
        return True

    monkeypatch.setattr(launcher, "run_guided_facebook_login", fake_guided_login)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: uvicorn_ports.append(int(kwargs["port"])),
    )

    try:
        exit_code = launcher.main(
            ["--data-dir", str(data_dir), "--no-open-browser", "--port", "8765"]
        )
    finally:
        reset_app_logging()

    assert exit_code == 0
    assert login_profile_dirs == [profile_dir]
    assert uvicorn_ports == [8765]


def test_launcher_does_not_start_web_ui_when_guided_login_is_cancelled(
    tmp_path,
    monkeypatch,
) -> None:
    """使用者關閉引導登入視窗時，launcher 不啟動 Web UI 也不標記 profile OK。"""

    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(launcher, "profile_has_facebook_session_cookies", lambda _path: False)
    monkeypatch.setattr(launcher, "run_guided_facebook_login", lambda _options: False)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("uvicorn should not run")
        ),
    )

    try:
        exit_code = launcher.main(
            ["--data-dir", str(data_dir), "--no-open-browser", "--port", "8765"]
        )
    finally:
        reset_app_logging()

    with SqliteApplicationContext(db_path) as app:
        status = app.repositories.app_settings.get_profile_session_status()
    assert exit_code == 2
    assert status.state != ProfileSessionState.OK


def test_launcher_does_not_start_web_ui_when_guided_login_fails(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """引導登入無法開啟時，launcher 保留 needs-login 狀態並回傳失敗。"""

    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(launcher, "profile_has_facebook_session_cookies", lambda _path: False)

    def fail_guided_login(_options: launcher.GuidedLoginOptions) -> bool:
        raise launcher.GuidedLoginError("profile busy")

    monkeypatch.setattr(launcher, "run_guided_facebook_login", fail_guided_login)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("uvicorn should not run")
        ),
    )

    try:
        exit_code = launcher.main(
            ["--data-dir", str(data_dir), "--no-open-browser", "--port", "8765"]
        )
    finally:
        reset_app_logging()

    with SqliteApplicationContext(db_path) as app:
        status = app.repositories.app_settings.get_profile_session_status()
    assert exit_code == 2
    assert status.state != ProfileSessionState.OK
    assert "無法開啟 Facebook 登入視窗：profile busy" in capsys.readouterr().out


def test_launcher_rejects_shared_db_profile_across_data_dirs(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    """不同 data-dir 只要共用實際 DB/profile，也不能同時啟動。"""

    shared_db = tmp_path / "shared" / "app.db"
    second_data_dir = tmp_path / "second"
    shared_profile = second_data_dir / "profiles" / "shared"
    shared_db.parent.mkdir()
    shared_profile.mkdir(parents=True)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
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
                    "--profile-dir",
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
    second_data_dir = tmp_path / "second-data"
    second_profile = second_data_dir / "profiles" / "second"
    shared_db.parent.mkdir()
    first_profile.mkdir(parents=True)
    second_profile.mkdir(parents=True)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
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
                    "--profile-dir",
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
    second_data_dir = tmp_path / "second-data"
    second_db = second_data_dir / "app.db"
    shared_profile = second_data_dir / "profiles" / "shared"
    first_db.parent.mkdir()
    second_db.parent.mkdir()
    shared_profile.mkdir(parents=True)
    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
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
                    "--profile-dir",
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
    second_data_dir = tmp_path / "second-data"
    second_db = second_data_dir / "app.db"
    first_profile = tmp_path / "profiles" / "first"
    second_profile = second_data_dir / "profiles" / "second"
    first_db.parent.mkdir()
    second_db.parent.mkdir()
    first_profile.mkdir(parents=True)
    second_profile.mkdir(parents=True)

    monkeypatch.setattr(
        launcher,
        "_run_uvicorn_with_shutdown_hook",
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
                    str(second_data_dir),
                    "--db-path",
                    str(second_db),
                    "--profile-dir",
                    str(second_profile),
                    "--auto-port",
                ]
            )
        finally:
            reset_app_logging()

    assert exit_code == 0
    assert len(seen_ports) == 1
