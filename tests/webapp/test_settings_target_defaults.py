"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext


from tests.webapp.app_test_helpers import create_app


def test_settings_page_updates_target_keyword_defaults(tmp_path: Path) -> None:
    """設定頁可保存新增 target 使用的排除字預設值。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    initial_response = client.get("/settings")
    save_response = client.post(
        "/settings/target-keywords",
        data={
            "exclude_keywords": "售完,暫停",
            "exclude_ignore_phrases": "全收;回收",
        },
        follow_redirects=False,
    )
    settings_response = client.get("/settings")
    new_target_response = client.get("/targets/new")

    assert initial_response.status_code == 200
    assert "關鍵字預設值" in initial_response.text
    assert "徵;收;已售" in initial_response.text
    assert "全收;回收" in initial_response.text
    assert save_response.status_code == 303
    assert "message=" in save_response.headers["location"]
    assert "售完,暫停" in settings_response.text
    assert "全收;回收" in settings_response.text
    assert 'name="exclude_keywords" type="hidden" value="售完,暫停"' in new_target_response.text
    assert (
        'name="exclude_ignore_phrases" type="hidden" value="全收;回收"' in new_target_response.text
    )
    with SqliteApplicationContext(db_path) as app_context:
        defaults = app_context.repositories.app_settings.get_target_keyword_defaults()
    assert defaults.exclude_keywords_text == "售完,暫停"
    assert defaults.exclude_ignore_phrases_text == "全收;回收"
