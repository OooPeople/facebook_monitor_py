"""FastAPI Web UI tests。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetMetadataStatus
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app


def test_index_renders_target_rename_modal(tmp_path: Path) -> None:
    """target card 更多選單提供更改名稱 dialog，且輸入框預填目前名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的票券社團",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "data-rename-target-button" in response.text
    assert "更改 target 名稱" in response.text
    assert 'name="display_name" type="text" value="我的票券社團"' in response.text


def test_index_hides_generated_fallback_name_until_metadata_refresh(tmp_path: Path) -> None:
    """metadata 尚未回填時，UI 顯示待抓取文案而不是系統 fallback id。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="204808657039646",
                parent_post_id="2155501991970293",
                canonical_url=(
                    "https://www.facebook.com/groups/204808657039646/posts/2155501991970293"
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "抓取社團名稱中，請稍後" in response.text
    assert 'name="display_name" type="text" value=""' in response.text
    assert "group:204808657039646:post:2155501991970293:comments" not in response.text


def test_metadata_refresh_updates_rename_modal_display_name(tmp_path: Path) -> None:
    """metadata refresh 補名後，read model 同步提供卡片標題與更名 modal 預填值。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="204808657039646",
                parent_post_id="2155501991970293",
                canonical_url=(
                    "https://www.facebook.com/groups/204808657039646/posts/2155501991970293"
                ),
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    pending_payload = client.get(f"/api/targets/{target.id}/card").json()
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.refresh_target_group_name(target.id, "票券測試社團")
    refreshed_payload = client.get(f"/api/targets/{target.id}/card").json()
    index_response = client.get("/")

    assert pending_payload["display_name"] == "抓取社團名稱中，請稍後"
    assert pending_payload["rename_display_name"] == ""
    assert refreshed_payload["display_name"] == "票券測試社團 / post:2155501991970293"
    assert refreshed_payload["rename_display_name"] == "票券測試社團 / post:2155501991970293"
    assert index_response.status_code == 200
    assert (
        'name="display_name" type="text" value="票券測試社團 / post:2155501991970293"'
    ) in index_response.text
    assert "group:204808657039646:post:2155501991970293:comments" not in index_response.text


def test_index_shows_metadata_failed_name_fallback(tmp_path: Path) -> None:
    """metadata 補名失敗時，UI 顯示手動改名提示並避免回填系統 fallback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="204808657039646",
                parent_post_id="2155501991970293",
                canonical_url=(
                    "https://www.facebook.com/groups/204808657039646/posts/2155501991970293"
                ),
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 尚未登入",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "無法自動抓取名稱，請手動更改名稱" in response.text
    assert 'name="display_name" type="text" value=""' in response.text
    assert "group:204808657039646:post:2155501991970293:comments" not in response.text


def test_update_target_name_route_updates_display_name(tmp_path: Path) -> None:
    """更改名稱 route 會更新 target.name 並回到原 target card。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="原本名稱",
                group_name="測試社團",
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "測試失敗",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/targets/{target.id}/name",
        data={
            "display_name": "(1) 新卡片名稱 | Facebook",
            "return_to": f"#target-{target.id}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
    assert loaded is not None
    assert loaded.name == "新卡片名稱"
    assert loaded.group_name == "測試社團"
    assert loaded.metadata_status == TargetMetadataStatus.RESOLVED
    assert loaded.metadata_error == ""


def test_manual_metadata_refresh_marks_pending_and_wakes_scheduler(
    tmp_path: Path,
) -> None:
    """設定 modal 的重新抓取會排入 resident metadata refresh，不直接搶 profile。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="舊名稱",
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 尚未登入",
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/metadata/refresh",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "%E5%B7%B2%E5%8A%A0%E5%85%A5%E6%8E%92%E7%A8%8B" in response.headers["location"]
    assert scheduler_manager.metadata_refresh_target_ids == [target.id]
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 1
    with SqliteApplicationContext(db_path) as app_context:
        updated = app_context.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.PENDING
    assert updated.metadata_error == ""


def test_cover_image_load_failure_queues_image_only_refresh(
    tmp_path: Path,
) -> None:
    """壞圖上報只排 image-only cover refresh，不改名稱 metadata 狀態。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="舊名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/api/targets/{target.id}/cover-image/load-failure",
        json={"url": "https://scontent.xx.fbcdn.net/old.jpg", "source": "card"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["queued"] is True
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 1
    assert scheduler_manager.metadata_refresh_target_ids == []
    second_response = client.post(
        f"/api/targets/{target.id}/cover-image/load-failure",
        json={"url": "https://scontent.xx.fbcdn.net/old.jpg", "source": "card"},
    )
    assert second_response.status_code == 200
    assert second_response.json()["status"] == "pending"
    assert second_response.json()["queued"] is False
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2
    with SqliteApplicationContext(db_path) as app_context:
        updated = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_result == "queued"


def test_cover_image_load_failure_ignores_stale_reported_url(
    tmp_path: Path,
) -> None:
    """舊 DOM 回報的壞圖 URL 若已非 DB 目前 URL，不應排新工作。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_cover_image_url="https://scontent.xx.fbcdn.net/new.jpg",
            )
        )

    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/api/targets/{target.id}/cover-image/load-failure",
        json={"url": "https://scontent.xx.fbcdn.net/old.jpg", "source": "sidebar"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored_stale_url"
    assert response.json()["queued"] is False
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0
    with SqliteApplicationContext(db_path) as app_context:
        state = app_context.repositories.cover_image_refreshes.get(target.id)
    assert state is None


def test_cover_image_load_failure_rejects_malformed_json(
    tmp_path: Path,
) -> None:
    """壞圖上報壞 JSON 應回 400，不留下 route 500。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_cover_image_url="https://scontent.xx.fbcdn.net/new.jpg",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.post(
        f"/api/targets/{target.id}/cover-image/load-failure",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "JSON 格式不正確"


def test_settings_modal_keeps_metadata_refresh_entry_hidden_for_later_placement(
    tmp_path: Path,
) -> None:
    """metadata refresh 入口放在設定 modal footer，不佔用設定內容區塊。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 尚未登入",
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))
    response = client.get("/")

    assert response.status_code == 200
    assert "Target 資訊" not in response.text
    assert "重新抓取名稱與封面" in response.text
    assert f"/targets/{target.id}/metadata/refresh" in response.text
