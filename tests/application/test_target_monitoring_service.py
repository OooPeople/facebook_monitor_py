"""Application service tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import UpdateTargetStatusRequest
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now


def test_start_and_stop_target_do_not_touch_other_targets(tmp_path: Path) -> None:
    """單一 target 開始/停止不會影響其他 target。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.restart_target_monitoring(second.id)

        stopped_first = app.services.targets.pause_target_monitoring(first.id)
        loaded_second = app.repositories.targets.get(second.id)
        first_runtime_state = app.repositories.runtime_states.get(first.id)
        second_runtime_state = app.repositories.runtime_states.get(second.id)

        assert stopped_first.enabled
        assert stopped_first.paused
        assert first_runtime_state is not None
        assert first_runtime_state.desired_state == TargetDesiredState.STOPPED
        assert loaded_second is not None
        assert loaded_second.enabled
        assert not loaded_second.paused
        assert second_runtime_state is not None
        assert second_runtime_state.desired_state == TargetDesiredState.ACTIVE

        started_first = app.services.targets.restart_target_monitoring(first.id)
        first_runtime_state = app.repositories.runtime_states.get(first.id)

        assert started_first.enabled
        assert not started_first.paused
        assert first_runtime_state is not None
        assert first_runtime_state.desired_state == TargetDesiredState.ACTIVE
        assert first_runtime_state.scan_requested_at is not None


def test_restart_monitoring_preserves_target_dedupe_state(tmp_path: Path) -> None:
    """開始監視只恢復排程，不清 seen/outbox 去重狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=first.scope_id, item_key="first-item", item_kind=ItemKind.POST)
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=second.scope_id, item_key="second-item", item_kind=ItemKind.POST)
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{first.id}:first-item:ntfy",
                target_id=first.id,
                item_key="first-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{second.id}:second-item:ntfy",
                target_id=second.id,
                item_key="second-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        app.services.targets.restart_target_monitoring(first.id)

        assert app.repositories.seen_items.has_seen(first.scope_id, "first-item")
        assert app.repositories.seen_items.has_seen(second.scope_id, "second-item")
        assert (
            app.repositories.notification_outbox.get_by_idempotency_key(
                f"{first.id}:first-item:ntfy"
            )
            is not None
        )
        assert (
            app.repositories.notification_outbox.get_by_idempotency_key(
                f"{second.id}:second-item:ntfy"
            )
            is not None
        )


def test_reset_target_notification_state_clears_target_outbox_and_seen(
    tmp_path: Path,
) -> None:
    """明確重置通知狀態會讓該 target 下輪可重新通知，但保留 history/scope。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=first.scope_id, item_key="first-item", item_kind=ItemKind.POST)
        )
        app.repositories.logical_items.mark_seen_aliases(
            target_id=first.id,
            item=SeenItem(
                scope_id=first.scope_id,
                item_key="first-item",
                item_kind=ItemKind.POST,
            ),
            item_keys=("first-item", "first-item-alias"),
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=second.scope_id, item_key="second-item", item_kind=ItemKind.POST)
        )
        app.repositories.scan_scope_state.mark_initialized(first.scope_id)
        app.repositories.match_history.add(
            MatchHistoryEntry(
                target_id=first.id,
                group_id=first.group_id,
                item_kind=ItemKind.POST,
                item_key="first-item",
                text="first",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{first.id}:first-item:ntfy",
                target_id=first.id,
                item_key="first-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{second.id}:second-item:ntfy",
                target_id=second.id,
                item_key="second-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        result = app.services.targets.reset_target_notification_state(first.id)

        assert result.notification_outbox_rows == 1
        assert result.seen_items == 1
        assert result.logical_seen_aliases == 2
        assert result.dedupe_epoch_before == 0
        assert result.dedupe_epoch_after == 1
        assert result.scan_scope_initialized_before is True
        assert result.scan_scope_initialized_after is True
        assert result.total_rows == 4
        assert not app.repositories.seen_items.has_seen(first.scope_id, "first-item")
        assert app.repositories.seen_items.has_seen(second.scope_id, "second-item")
        assert app.repositories.scan_scope_state.is_initialized(first.scope_id)
        assert len(app.repositories.match_history.list_by_target(first.id)) == 1
        assert (
            app.repositories.notification_outbox.get_by_idempotency_key(
                f"{first.id}:first-item:ntfy"
            )
            is None
        )
        assert (
            app.repositories.notification_outbox.get_by_idempotency_key(
                f"{second.id}:second-item:ntfy"
            )
            is not None
        )


def test_reset_target_notification_state_releases_baseline_suppression(
    tmp_path: Path,
) -> None:
    """重置通知狀態是使用者明確重播意圖，需讓下一輪不再 baseline suppress。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.repositories.scan_scope_state.clear_scope(target.scope_id)

        assert not app.repositories.scan_scope_state.is_initialized(target.scope_id)

        result = app.services.targets.reset_target_notification_state(target.id)

        assert result.notification_outbox_rows == 0
        assert result.seen_items == 0
        assert result.logical_seen_aliases == 0
        assert result.dedupe_epoch_before == 0
        assert result.dedupe_epoch_after == 1
        assert result.scan_scope_initialized_before is False
        assert result.scan_scope_initialized_after is True
        assert app.repositories.scan_scope_state.is_initialized(target.scope_id)


def test_restart_monitoring_preserves_uninitialized_scan_scope(tmp_path: Path) -> None:
    """開始監視不主動略過第一次 baseline scan。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.repositories.scan_scope_state.clear_scope(target.scope_id)

        app.services.targets.restart_target_monitoring(target.id)

        assert not app.repositories.scan_scope_state.is_initialized(target.scope_id)


def test_restart_comments_monitoring_preserves_comments_seen_scope(tmp_path: Path) -> None:
    """comments target 開始監視時也會保留 seen scope 並要求立即掃描。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="111",
                parent_post_id="999",
                canonical_url="https://www.facebook.com/groups/111/posts/999",
            )
        )
        other = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="comment-before-start",
                item_kind=ItemKind.COMMENT,
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=other.scope_id, item_key="post-before-start", item_kind=ItemKind.POST)
        )

        started = app.services.targets.restart_target_monitoring(target.id)
        state = app.repositories.runtime_states.get(target.id)

        assert started.enabled
        assert not started.paused
        assert state is not None
        assert state.desired_state == TargetDesiredState.ACTIVE
        assert state.scan_requested_at is not None
        assert app.repositories.seen_items.has_seen(target.scope_id, "comment-before-start")
        assert app.repositories.seen_items.has_seen(other.scope_id, "post-before-start")


def test_pause_monitoring_preserves_seen_scope(tmp_path: Path) -> None:
    """停止監視只停排程，不清 seen scope。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.repositories.seen_items.mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="item-1", item_kind=ItemKind.POST)
        )

        app.services.targets.pause_target_monitoring(target.id)

        assert app.repositories.seen_items.has_seen(target.scope_id, "item-1")


def test_webui_startup_pause_all_targets_without_overwriting_floating_interval(
    tmp_path: Path,
) -> None:
    """Web UI 啟動整理會停止所有 target，但不把浮動刷新改回固定。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(
                    fixed_refresh_sec=None,
                    min_refresh_sec=25,
                    max_refresh_sec=35,
                    jitter_enabled=True,
                ),
            )
        )

        app.services.targets.pause_all_targets_for_webui_startup()

        loaded = app.repositories.targets.get(target.id)
        state = app.repositories.runtime_states.get(target.id)
        config = app.repositories.configs.get_for_target(target)

    assert loaded is not None
    assert loaded.paused
    assert state is not None
    assert state.desired_state == TargetDesiredState.STOPPED
    assert config is not None
    assert config.fixed_refresh_sec is None
    assert config.jitter_enabled
    assert config.min_refresh_sec == 25
    assert config.max_refresh_sec == 35


def test_update_target_status_request(tmp_path: Path) -> None:
    """可透過明確 request 更新 target enabled/paused 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )

        updated = app.services.targets.update_target_status(
            UpdateTargetStatusRequest(
                target_id=target.id,
                enabled=False,
                paused=True,
            )
        )

        assert not updated.enabled
        assert updated.paused


def test_recover_stale_running_targets_restarts_old_heartbeat(tmp_path: Path) -> None:
    """application service 會重啟過舊 running state，避免 target 永久卡住。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        stale_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        fresh_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        stale_state = app.services.targets.mark_target_running(stale_target.id, "old-worker")
        fresh_state = app.services.targets.mark_target_running(fresh_target.id, "new-worker")
        app.repositories.runtime_states.save(
            replace(
                stale_state,
                last_heartbeat_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )
        app.repositories.runtime_states.save(
            replace(
                fresh_state,
                last_heartbeat_at=now - timedelta(seconds=30),
                updated_at=now - timedelta(seconds=30),
            )
        )

        recovered = app.services.targets.recover_stale_running_targets(
            stale_after_seconds=180,
            now=now,
        )
        loaded_stale = app.repositories.runtime_states.get(stale_target.id)
        loaded_fresh = app.repositories.runtime_states.get(fresh_target.id)

    assert len(recovered) == 1
    assert loaded_stale is not None
    assert loaded_fresh is not None
    assert loaded_stale.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded_stale.scan_requested_at == now
    assert loaded_stale.active_worker_id == ""
    assert loaded_stale.last_error == ""
    assert loaded_stale.last_skip_reason == "target_page_restart: retry 1/3"
    assert loaded_stale.consecutive_failure_reason == "stale_running"
    assert loaded_stale.consecutive_failure_count == 1
    assert recovered[0].previous_worker_id == "old-worker"
    assert recovered[0].decision.auto_restart
    assert loaded_fresh.runtime_status == TargetRuntimeStatus.RUNNING


def test_stale_running_recovery_does_not_overwrite_refreshed_owner(
    tmp_path: Path,
) -> None:
    """stale recovery 的條件更新不可覆蓋已刷新 heartbeat 的同一 worker。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stale-race",
                canonical_url="https://www.facebook.com/groups/stale-race",
            )
        )
        running = app.services.targets.mark_target_running(target.id, "worker")
        assert running.last_started_at is not None
        stale_snapshot = replace(
            running,
            last_heartbeat_at=now - timedelta(seconds=240),
            updated_at=now - timedelta(seconds=240),
        )
        app.repositories.runtime_states.save(stale_snapshot)
        refreshed = app.services.targets.record_target_heartbeat_if_owner(
            target.id,
            worker_id="worker",
            started_at=running.last_started_at,
        )
        stale_error = replace(
            stale_snapshot,
            runtime_status=TargetRuntimeStatus.ERROR,
            last_error="stale",
            active_worker_id="",
            updated_at=now,
        )
        committed = app.repositories.runtime_states.save_stale_running_error_if_unchanged(
            stale_error,
            worker_id="worker",
            started_at=running.last_started_at,
            stale_before=now - timedelta(seconds=180),
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert refreshed is not None
    assert committed is None
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.RUNNING
    assert loaded.active_worker_id == "worker"
    assert loaded.last_error == ""


def test_recover_stale_queued_targets_returns_to_idle_for_retry(tmp_path: Path) -> None:
    """排隊過久的 target 會回到 idle 並保留立即掃描請求。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.request_target_scan(target.id)
        queued_state = app.services.targets.mark_target_queued(target.id, "manual_request")
        app.repositories.runtime_states.save(
            replace(
                queued_state,
                last_enqueued_at=now - timedelta(seconds=240),
                updated_at=now - timedelta(seconds=240),
            )
        )

        recovered = app.services.targets.recover_stale_queued_targets(
            stale_after_seconds=180,
            now=now,
        )
        loaded = app.repositories.runtime_states.get(target.id)

    assert len(recovered) == 1
    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded.scan_requested_at is not None
    assert "監視項目排隊等待過久" in loaded.last_skip_reason


def test_delete_target_does_not_touch_other_targets(tmp_path: Path) -> None:
    """刪除單一 target 不會影響其他 target 與其狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
            )
        )
        second = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
            )
        )
        app.services.targets.pause_target_monitoring(second.id)

        app.services.targets.delete_target(first.id)

        assert app.repositories.targets.get(first.id) is None
        loaded_second = app.repositories.targets.get(second.id)
        assert loaded_second is not None
        assert loaded_second.enabled
        assert loaded_second.paused
