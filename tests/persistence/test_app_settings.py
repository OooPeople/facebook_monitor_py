"""App settings repository tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState


def test_profile_session_status_round_trips(tmp_path: Path) -> None:
    """profile session status 會保存 needs_login reason 與來源。"""

    db_path = tmp_path / "app.db"

    with SqliteApplicationContext(db_path) as app:
        initial = app.repositories.app_settings.get_profile_session_status()
        needs_login = app.repositories.app_settings.mark_profile_needs_login(
            reason="login_required",
            source="resident_main",
        )

    with SqliteApplicationContext(db_path) as app:
        reloaded = app.repositories.app_settings.get_profile_session_status()
        ok = app.repositories.app_settings.mark_profile_ok(source="launcher_guided_login")

    assert initial.state == ProfileSessionState.UNKNOWN
    assert not initial.needs_login
    assert needs_login.state == ProfileSessionState.NEEDS_LOGIN
    assert needs_login.needs_login
    assert needs_login.reason == "login_required"
    assert needs_login.source == "resident_main"
    assert needs_login.updated_at is not None
    assert reloaded == needs_login
    assert ok.state == ProfileSessionState.OK
    assert not ok.needs_login
    assert ok.source == "launcher_guided_login"
