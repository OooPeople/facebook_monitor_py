"""FastAPI Web UI tests。"""

from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from fastapi.testclient import TestClient

from facebook_monitor.application import context as application_context
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.webapp.dashboard_queries import list_sidebar_items
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable
from facebook_monitor.webapp.dashboard_revision_query import get_dashboard_revision
from facebook_monitor.webapp.routes import dashboard as dashboard_routes
from facebook_monitor.webapp.routes.dashboard import _format_dashboard_revision_event


from tests.webapp.app_test_helpers import create_app


def test_dashboard_revision_endpoint_changes_after_target_update(tmp_path: Path) -> None:
    """dashboard revision endpoint 只在資料有變更時供前端刷新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    first_revision = client.get("/api/dashboard-revision").json()["revision"]
    response = client.post(
        f"/targets/{target.id}/config",
        data={
            "return_to": f"#target-{target.id}",
            "include_keywords": "票券",
            "exclude_keywords": "",
            "fixed_refresh_sec": "60",
            "max_items_per_scan": "5",
        },
        follow_redirects=False,
    )
    second_revision = client.get("/api/dashboard-revision").json()["revision"]

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    assert first_revision != second_revision


def test_dashboard_revision_read_path_does_not_initialize_schema(tmp_path: Path) -> None:
    """SSE revision read path 不應為空 DB 建立 schema 或檔案。"""

    db_path = tmp_path / "app.db"

    revision = get_dashboard_revision(db_path)

    assert revision.revision == "0"
    assert not db_path.exists()


def test_dashboard_sidebar_read_path_does_not_initialize_schema(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sidebar partial update read path 不應在掃描寫入期間重跑 schema init。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("dashboard read path should not initialize schema")

    monkeypatch.setattr(application_context, "initialize_schema", fail_if_called)

    items = list_sidebar_items(db_path)

    assert len(items) == 1


def test_dashboard_revision_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """dashboard polling endpoint 遇到短暫 DB lock 時回 503，前端可忽略該輪。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardRevisionUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_revision", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/dashboard-revision")

    assert response.status_code == 503


def test_dashboard_page_uses_dashboard_route_view_loader_seam(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Dashboard page read model seam 留在 routes.dashboard，避免 split 後失控。"""

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_dashboard_view", raise_locked)
    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/")

    assert response.status_code == 503


def test_dashboard_sidebar_endpoint_ignores_temporary_sqlite_lock(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Sidebar partial update 遇到短暫 DB lock 時回 503，避免 ASGI traceback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path):
        pass

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "list_sidebar_items", raise_locked)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    response = client.get("/api/sidebar")

    assert response.status_code == 503


def test_dashboard_card_uses_dashboard_route_target_card_loader_seam(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Target card partial read model seam 留在 routes.dashboard。"""

    def raise_locked(*args: object, **kwargs: object) -> object:
        raise DashboardReadUnavailable("database is locked")

    monkeypatch.setattr(dashboard_routes, "get_target_card", raise_locked)
    client = TestClient(create_app(db_path=tmp_path / "app.db", profile_dir=tmp_path / "profile"))

    response = client.get("/api/targets/missing/card")

    assert response.status_code == 503


def test_dashboard_events_streams_revision_event(tmp_path: Path) -> None:
    """dashboard event stream endpoint 與 event 格式會提供 revision event。"""

    db_path = tmp_path / "app.db"
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    openapi = client.get("/openapi.json").json()
    event_text = _format_dashboard_revision_event(
        {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}
    )

    assert "/api/dashboard-events" in openapi["paths"]
    assert event_text.startswith("event: dashboard_revision\n")
    assert event_text.endswith("\n\n")
    data_line = next(line for line in event_text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload == {"revision": "rev-1", "last_changed_at": "2026-05-08T00:00:00"}
