"""Launcher single-instance behavior tests。"""

from __future__ import annotations


from facebook_monitor import launcher
from facebook_monitor.runtime.instance_lock import AppInstanceLockError
from facebook_monitor.runtime.instance_lock import ServerInfo
from facebook_monitor.runtime.logging_setup import reset_app_logging

def test_launcher_reuses_runtime_csrf_token_between_restarts(tmp_path, monkeypatch) -> None:
    """同一 data-dir 重新啟動後，舊 dashboard form token 仍可通過 CSRF 驗證。"""

    data_dir = tmp_path / "data"
    csrf_tokens: list[str] = []

    def fake_uvicorn_run(app, *args: object, **kwargs: object) -> None:
        csrf_tokens.append(str(app.state.csrf_token))

    monkeypatch.setattr(launcher, "_run_uvicorn_with_shutdown_hook", fake_uvicorn_run)

    try:
        first_exit_code = launcher.main(
            ["--data-dir", str(data_dir), "--port", "8767", "--no-open-browser"]
        )
        second_exit_code = launcher.main(
            ["--data-dir", str(data_dir), "--port", "8767", "--no-open-browser"]
        )
    finally:
        reset_app_logging()

    assert first_exit_code == 0
    assert second_exit_code == 0
    assert len(csrf_tokens) == 2
    assert csrf_tokens[1] == csrf_tokens[0]
    assert (data_dir / "runtime" / "csrf_token.txt").read_text(
        encoding="utf-8"
    ).strip() == csrf_tokens[0]


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
        launcher,
        "_run_uvicorn_with_shutdown_hook",
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
