"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext


from tests.webapp.app_test_helpers import create_app


def test_theme_preference_is_stored_in_database_for_all_pages(tmp_path: Path) -> None:
    """主題偏好必須存進 app DB，避免 auto-port 或瀏覽器狀態遺失。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    initial_response = client.get("/")
    save_response = client.post("/settings/theme", json={"theme": "dark"})
    index_response = client.get("/")
    settings_response = client.get("/settings")
    new_target_response = client.get("/targets/new")

    assert initial_response.status_code == 200
    assert '<meta name="app-theme" content="dark">' in initial_response.text
    assert save_response.status_code == 200
    assert save_response.json() == {"theme": "dark"}
    assert '<meta name="app-theme" content="dark">' in index_response.text
    assert '<meta name="app-theme" content="dark">' in settings_response.text
    assert '<meta name="app-theme" content="dark">' in new_target_response.text
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.app_settings.get_theme() == "dark"


def test_theme_preference_rejects_unknown_value(tmp_path: Path) -> None:
    """theme API 不接受未定義值。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post("/settings/theme", json={"theme": "system"})

    assert response.status_code == 400


def test_theme_preference_rejects_malformed_json(tmp_path: Path) -> None:
    """theme API 壞 JSON 應回 400，不可讓解析例外冒成 500。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post(
        "/settings/theme",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "JSON 格式不正確"


def test_theme_preference_rejects_non_object_json_payload(tmp_path: Path) -> None:
    """共用 JSON payload helper 不接受 array/string 這類非物件 payload。"""

    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.post("/settings/theme", json=["dark"])

    assert response.status_code == 400
    assert response.json()["detail"] == "JSON payload 必須是物件"
