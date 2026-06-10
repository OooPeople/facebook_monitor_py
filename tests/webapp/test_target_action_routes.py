"""FastAPI Web UI tests。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pytest import MonkeyPatch
from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.webapp.routes import target_actions as target_action_routes
from tests.helpers.webapp import FakeSchedulerManager


from tests.webapp.app_test_helpers import create_app
from tests.webapp.app_test_helpers import page_feedback


def test_scheduler_routes_are_not_public_daily_controls(tmp_path: Path) -> None:
    """Web UI 不再提供全域 scheduler 日常主開關 route。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    start_response = client.post("/scheduler/start", follow_redirects=False)
    index_response = client.get("/")
    stop_response = client.post("/scheduler/stop", follow_redirects=False)

    assert start_response.status_code == 404
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.options is None
    assert "背景掃描服務" not in index_response.text
    assert "啟動自動掃描" not in index_response.text
    assert "停止自動掃描" not in index_response.text
    assert stop_response.status_code == 404
    assert scheduler_manager.stopped_count == 0
    assert not scheduler_manager.running


def test_webui_startup_resets_targets_to_stopped(tmp_path: Path) -> None:
    """正式 Web UI 啟動時會停止 target，但不覆蓋浮動刷新設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                ),
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_targets_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "已停止" in response.text
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state.value == "stopped"
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == 25
    assert config.max_refresh_sec == 35


def test_webui_startup_can_clear_runtime_debug_data(tmp_path: Path) -> None:
    """Web UI 啟動時可清除上一輪 runtime/debug data，保留 target 設定。"""

    db_path = tmp_path / "app.db"
    scheduler_manager = FakeSchedulerManager()
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.scan_scope_state.mark_initialized(target.scope_id)
        logical = app_context.repositories.logical_items.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
            ),
            item_keys=("seen-before-startup",),
        )
        app_context.repositories.notification_dedupe.reserve_match(
            target_id=target.id,
            logical_item_id=logical.logical_item_id,
            item_key="seen-before-startup",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-before-startup:ntfy",
                target_id=target.id,
                item_key="seen-before-startup",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        scan_run_id = app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=1,
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_run_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-before-startup",
                    item_index=0,
                )
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-before-startup",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )

    with TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            reset_runtime_data_on_startup=True,
        )
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        config = app_context.repositories.configs.get_for_target(target)
        latest_scan = app_context.repositories.scan_runs.latest_by_target(target.id)
        latest_items = app_context.repositories.latest_scan_items.list_by_target(target.id)
        notifications = app_context.repositories.notification_events.list_by_target(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-startup",
        )
        scope_initialized = app_context.repositories.scan_scope_state.is_initialized(
            target.scope_id
        )
        logical_alias_count = app_context.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM logical_item_aliases WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        dedupe_count = app_context.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM notification_dedupe WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:seen-before-startup:ntfy"
        )

    assert loaded is not None
    assert config is not None
    assert latest_scan is None
    assert latest_items == []
    assert notifications == []
    assert has_seen
    assert scope_initialized
    assert logical_alias_count == 1
    assert dedupe_count == 1
    assert outbox is not None


def test_start_and_stop_routes_update_target_status(tmp_path: Path) -> None:
    """Web UI 開始/停止 route 對齊 restart/pause 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-before-start:ntfy",
                target_id=target.id,
                item_key="seen-before-start",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    stop_response = client.post(f"/targets/{target.id}/stop", follow_redirects=False)
    start_response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert stop_response.status_code == 303
    assert start_response.status_code == 303
    assert start_response.headers["location"].endswith(f"#target-{target.id}")
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "seen-before-start",
        )
        outbox_entry = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:seen-before-start:ntfy",
        )
    assert loaded is not None
    assert loaded.enabled
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert has_seen
    assert outbox_entry is not None
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2


def test_reset_target_notification_state_route_clears_outbox_and_seen(
    tmp_path: Path,
) -> None:
    """target 更多操作會重置通知與 seen 去重狀態，但不喚醒 scheduler。"""

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
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=first.scope_id,
                item_key="first-seen",
                item_kind=ItemKind.POST,
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=second.scope_id,
                item_key="second-seen",
                item_kind=ItemKind.POST,
            )
        )
        for target, item_key in ((first, "first-seen"), (second, "second-seen")):
            app_context.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:{item_key}:ntfy",
                    target_id=target.id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                )
            )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{first.id}/notifications/clear",
        data={"return_to": f"#target-{first.id}"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert (
        page_feedback(response.text)["message"]
        == "已重置通知狀態：清除通知紀錄 1 筆、已看紀錄 1 筆"
    )
    assert page_feedback(response.text)["feedback"] == "notification_state_reset"
    with SqliteApplicationContext(db_path) as app_context:
        first_seen = app_context.repositories.seen_items.has_seen(
            first.scope_id,
            "first-seen",
        )
        first_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{first.id}:first-seen:ntfy",
        )
        second_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{second.id}:second-seen:ntfy",
        )
        second_seen = app_context.repositories.seen_items.has_seen(
            second.scope_id,
            "second-seen",
        )
    assert not first_seen
    assert second_seen
    assert first_outbox is None
    assert second_outbox is not None
    assert scheduler_manager.woken_count == 0


def test_sidebar_group_start_and_stop_routes_update_only_group_targets(
    tmp_path: Path,
) -> None:
    """sidebar group 開始/停止批次套用 target 語義，且只喚醒 scheduler 一次。"""

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
        outside = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="333",
                canonical_url="https://www.facebook.com/groups/333",
            )
        )
        group = app_context.services.sidebar_layout.create_group("批次操作")
        app_context.services.sidebar_layout.save_placements(
            [
                (group.id, [first.id, second.id]),
                (None, [outside.id]),
            ]
        )
        app_context.services.targets.restart_target_monitoring(second.id)
        app_context.services.targets.restart_target_monitoring(outside.id)
        for target, item_key in (
            (first, "first-seen"),
            (second, "second-seen"),
            (outside, "outside-seen"),
        ):
            app_context.repositories.seen_items.mark_seen(
                SeenItem(
                    scope_id=target.scope_id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                )
            )
            app_context.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:{item_key}:desktop",
                    target_id=target.id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.DESKTOP,
                    title="title",
                    message="message",
                )
            )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    stop_response = client.post(f"/api/sidebar/groups/{group.id}/stop")

    assert stop_response.status_code == 200
    assert stop_response.json()["updated_count"] == 2
    with SqliteApplicationContext(db_path) as app_context:
        first_stopped = app_context.repositories.targets.get(first.id)
        second_stopped = app_context.repositories.targets.get(second.id)
        outside_after_stop = app_context.repositories.targets.get(outside.id)
        first_seen_after_stop = app_context.repositories.seen_items.has_seen(
            first.scope_id,
            "first-seen",
        )
        first_outbox_after_stop = (
            app_context.repositories.notification_outbox.get_by_idempotency_key(
                f"{first.id}:first-seen:desktop",
            )
        )
    assert first_stopped is not None and first_stopped.enabled and first_stopped.paused
    assert second_stopped is not None and second_stopped.enabled and second_stopped.paused
    assert outside_after_stop is not None and outside_after_stop.enabled
    assert not outside_after_stop.paused
    assert first_seen_after_stop
    assert first_outbox_after_stop is not None

    start_response = client.post(f"/api/sidebar/groups/{group.id}/start")

    assert start_response.status_code == 200
    assert start_response.json()["updated_count"] == 2
    assert scheduler_manager.woken_count == 2
    with SqliteApplicationContext(db_path) as app_context:
        first_loaded = app_context.repositories.targets.get(first.id)
        second_loaded = app_context.repositories.targets.get(second.id)
        outside_loaded = app_context.repositories.targets.get(outside.id)
        first_state = app_context.repositories.runtime_states.get(first.id)
        second_state = app_context.repositories.runtime_states.get(second.id)
        outside_state = app_context.repositories.runtime_states.get(outside.id)
        first_seen = app_context.repositories.seen_items.has_seen(
            first.scope_id,
            "first-seen",
        )
        second_seen = app_context.repositories.seen_items.has_seen(
            second.scope_id,
            "second-seen",
        )
        outside_seen = app_context.repositories.seen_items.has_seen(
            outside.scope_id,
            "outside-seen",
        )
        first_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{first.id}:first-seen:desktop",
        )
        second_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{second.id}:second-seen:desktop",
        )
        outside_outbox = app_context.repositories.notification_outbox.get_by_idempotency_key(
            f"{outside.id}:outside-seen:desktop",
        )

    assert first_loaded is not None and first_loaded.enabled and not first_loaded.paused
    assert second_loaded is not None and second_loaded.enabled and not second_loaded.paused
    assert outside_loaded is not None and outside_loaded.enabled and not outside_loaded.paused
    assert first_state is not None and first_state.scan_requested_at is not None
    assert second_state is not None and second_state.scan_requested_at is not None
    assert outside_state is not None and outside_state.scan_requested_at is not None
    assert first_seen
    assert second_seen
    assert outside_seen
    assert first_outbox is not None
    assert second_outbox is not None
    assert outside_outbox is not None
    assert scheduler_manager.started_count == 1


def test_start_route_supports_comments_target(tmp_path: Path) -> None:
    """Web UI comments target 的開始 route 保留 comments seen 並喚醒 scheduler。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )
        app_context.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(
        f"/targets/{target.id}/start",
        data={"return_to": f"#target-{target.id}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        loaded = app_context.repositories.targets.get(target.id)
        state = app_context.repositories.runtime_states.get(target.id)
        has_seen = app_context.repositories.seen_items.has_seen(
            target.scope_id,
            "comment-before-start",
        )
    assert loaded is not None
    assert not loaded.paused
    assert state is not None
    assert state.scan_requested_at is not None
    assert has_seen
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 1


def test_scan_once_requests_resident_scan_for_posts_and_comments(tmp_path: Path) -> None:
    """Web UI scan-once 只排入 resident scan request，不啟動 one-shot fallback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        posts_target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        comments_target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )
        app_context.services.targets.restart_target_monitoring(posts_target.id)
        app_context.services.targets.restart_target_monitoring(comments_target.id)

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    posts_response = client.post(f"/targets/{posts_target.id}/scan-once", follow_redirects=False)
    comments_response = client.post(
        f"/targets/{comments_target.id}/scan-once",
        follow_redirects=False,
    )

    assert posts_response.status_code == 303
    assert comments_response.status_code == 303
    with SqliteApplicationContext(db_path) as app_context:
        posts_state = app_context.repositories.runtime_states.get(posts_target.id)
        comments_state = app_context.repositories.runtime_states.get(comments_target.id)
    assert posts_state is not None
    assert posts_state.scan_requested_at is not None
    assert comments_state is not None
    assert comments_state.scan_requested_at is not None
    assert scheduler_manager.started_count == 1
    assert scheduler_manager.woken_count == 2


def test_scan_once_requires_started_target(tmp_path: Path) -> None:
    """停止中的 target 不會被 scan-once 暗中送進 fallback worker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="222518561920110",
                parent_post_id="2187454285426518",
                canonical_url=(
                    "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
                ),
            )
        )

    scheduler_manager = FakeSchedulerManager()
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )
    response = client.post(f"/targets/{target.id}/scan-once", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0


def test_target_action_db_failure_does_not_trigger_scheduler_side_effect(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """target action DB operation 失敗時不得喚醒或啟動 scheduler。"""

    async def raise_locked(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("database is locked")

    scheduler_manager = FakeSchedulerManager()
    monkeypatch.setattr(target_action_routes, "run_web_db_operation", raise_locked)
    client = TestClient(
        create_app(
            db_path=tmp_path / "app.db",
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
        )
    )

    response = client.post("/targets/target-a/start", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert scheduler_manager.started_count == 0
    assert scheduler_manager.woken_count == 0
