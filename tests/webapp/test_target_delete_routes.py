"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest


from tests.webapp.app_test_helpers import create_app


def test_delete_route_removes_only_selected_target(tmp_path: Path) -> None:
    """Web UI 刪除 route 只刪除指定 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        first = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app_context.services.targets.pause_target_monitoring(second.id)

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(f"/targets/{first.id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        assert app_context.repositories.targets.get(first.id) is None
        loaded_second = app_context.repositories.targets.get(second.id)
    assert loaded_second is not None
    assert loaded_second.enabled
    assert loaded_second.paused
