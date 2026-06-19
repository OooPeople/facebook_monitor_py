"""Shared scan finalize tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.worker.scan_finalize import NormalizedScanItem
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure

from tests.worker.scan_finalize_test_helpers import finalize_scan_items
from tests.worker.scan_finalize_test_helpers import dispatch_pending_notifications_for_test
from tests.worker.scan_finalize_test_helpers import _activate_target


def test_restart_monitoring_preserves_previously_seen_match_notification_state(
    tmp_path: Path,
) -> None:
    """停止後再開始不重播已看過且已通知過的命中。"""

    sent_ntfy: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_ntfy.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        first_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert first_result.new_count == 1
        assert first_result.matched_count == 1
        assert not first_result.baseline_mode
        pending_outbox = app.repositories.notification_outbox.list_pending()
        assert len(pending_outbox) == 1
        assert pending_outbox[0].dedupe_id is not None
        dedupe_count = app.repositories.notification_outbox.connection.execute(
            """
            SELECT COUNT(*)
            FROM notification_dedupe
            WHERE target_id = ?
              AND channel = 'ntfy'
            """,
            (target.id,),
        ).fetchone()[0]
        assert dedupe_count == 1
        assert len(app.repositories.match_history.list_by_target(target.id)) == 1
        target_id = target.id
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test"]

    with SqliteApplicationContext(db_path) as app:
        reloaded_target = app.repositories.targets.get(target_id)
        assert reloaded_target is not None
        app.services.targets.pause_target_monitoring(reloaded_target.id)
        app.services.targets.restart_target_monitoring(reloaded_target.id)
        config = app.services.targets.get_config_for_target(reloaded_target)
        second_result = finalize_scan_items(
            app=app,
            target=reloaded_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert second_result.new_count == 0
        assert second_result.matched_count == 1
        assert not second_result.baseline_mode
        assert (
            second_result.latest_items[0].debug_metadata["classification"]["eligible_for_notify"]
            is False
        )
        assert second_result.notification_payloads == ()
        assert len(app.repositories.notification_outbox.list_pending()) == 0
        assert len(app.repositories.match_history.list_by_target(reloaded_target.id)) == 1

    assert sent_ntfy == ["phase0test"]


def test_finalize_keeps_dedupe_after_terminal_outbox_retention(
    tmp_path: Path,
) -> None:
    """terminal outbox 被 retention 清掉後，下一輪仍不重送同一 logical item。"""

    sent_ntfy: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_ntfy.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        first_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )
        pending_outbox = app.repositories.notification_outbox.list_pending()
        assert len(pending_outbox) == 1
        assert pending_outbox[0].id is not None
        app.repositories.notification_outbox.mark_result(
            entry_id=pending_outbox[0].id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
        )
        app.repositories.notification_outbox.connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = ?
            """,
            (encode_datetime(utc_now() - timedelta(days=8)),),
        )
        retention_result = app.repositories.maintenance.prune_bounded_retention(now=utc_now())
        remaining_outbox_count = app.repositories.notification_outbox.connection.execute(
            "SELECT COUNT(*) FROM notification_outbox"
        ).fetchone()[0]
        remaining_dedupe_count = app.repositories.notification_outbox.connection.execute(
            "SELECT COUNT(*) FROM notification_dedupe"
        ).fetchone()[0]
        app.repositories.notification_outbox.connection.commit()
        second_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

    assert first_result.new_count == 1
    assert len(first_result.notification_payloads) == 1
    assert retention_result.terminal_outbox == 1
    assert remaining_outbox_count == 0
    assert remaining_dedupe_count == 1
    assert second_result.new_count == 0
    assert second_result.notification_payloads == ()
    assert sent_ntfy == []


def test_reset_notification_state_allows_previously_seen_match_to_notify_again(
    tmp_path: Path,
) -> None:
    """使用者明確重置通知狀態後，已看過命中可在非 baseline 掃描再次通知。"""

    sent_ntfy: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_ntfy.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        first_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert first_result.new_count == 1
        assert not first_result.baseline_mode
        first_epoch = app.repositories.dedupe_state.peek_current_epoch(target.id)
        target_id = target.id
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test"]

    with SqliteApplicationContext(db_path) as app:
        reloaded_target = app.repositories.targets.get(target_id)
        assert reloaded_target is not None
        clear_result = app.services.targets.reset_target_notification_state(reloaded_target.id)
        assert first_epoch == 0
        assert app.repositories.dedupe_state.peek_current_epoch(reloaded_target.id) == 1
        assert len(app.repositories.match_history.list_by_target(reloaded_target.id)) == 1
        assert app.repositories.scan_scope_state.is_initialized(reloaded_target.scope_id)
        config = app.services.targets.get_config_for_target(reloaded_target)
        second_result = finalize_scan_items(
            app=app,
            target=reloaded_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert clear_result.notification_outbox_rows == 1
        assert clear_result.seen_items == 1
        assert clear_result.logical_seen_aliases == 1
        assert clear_result.dedupe_epoch_before == 0
        assert clear_result.dedupe_epoch_after == 1
        assert clear_result.scan_scope_initialized_before is True
        assert clear_result.scan_scope_initialized_after is True
        assert second_result.new_count == 1
        assert not second_result.baseline_mode
        assert (
            second_result.latest_items[0].debug_metadata["classification"]["eligible_for_notify"]
            is True
        )
        assert len(second_result.notification_payloads) == 1
        dedupe_epochs = {
            int(row["dedupe_epoch"])
            for row in app.repositories.notification_outbox.connection.execute(
                """
                SELECT dedupe_epoch
                FROM notification_dedupe
                WHERE target_id = ?
                """,
                (reloaded_target.id,),
            ).fetchall()
        }
        assert dedupe_epochs == {0, 1}
        assert len(app.repositories.match_history.list_by_target(reloaded_target.id)) == 1
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test", "phase0test"]


def test_startup_runtime_cleanup_preserves_notification_state(
    tmp_path: Path,
) -> None:
    """啟動清理不應重置 baseline 或 seen，避免新命中被第一輪吃掉。"""

    sent_ntfy: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_ntfy.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        first_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )
        target_id = target.id

        assert first_result.new_count == 1
        assert not first_result.baseline_mode
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test"]

    with SqliteApplicationContext(db_path) as app:
        reloaded_target = app.repositories.targets.get(target_id)
        assert reloaded_target is not None
        cleanup_result = app.repositories.maintenance.clear_startup_runtime_data()

        assert cleanup_result.seen_items == 0
        assert cleanup_result.scan_scope_state == 0
        assert app.repositories.scan_scope_state.is_initialized(reloaded_target.scope_id)
        config = app.services.targets.get_config_for_target(reloaded_target)
        new_match_result = finalize_scan_items(
            app=app,
            target=reloaded_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:new-after-startup",
                    alias_keys=("post:new-after-startup",),
                    group_id="123",
                    text="新出現的票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )
        repeat_result = finalize_scan_items(
            app=app,
            target=reloaded_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert new_match_result.new_count == 1
        assert not new_match_result.baseline_mode
        assert (
            new_match_result.latest_items[0].debug_metadata["classification"]["eligible_for_notify"]
            is True
        )
        assert len(new_match_result.notification_payloads) == 1
        assert repeat_result.new_count == 0
        assert not repeat_result.baseline_mode
        assert (
            repeat_result.latest_items[0].debug_metadata["classification"]["eligible_for_notify"]
            is False
        )
        assert repeat_result.notification_payloads == ()
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test", "phase0test"]


def test_reset_notification_state_after_startup_runtime_cleanup_notifies_seen_match(
    tmp_path: Path,
) -> None:
    """啟動清理保留狀態；使用者明確 reset 後，同一命中才可重新通知。"""

    sent_ntfy: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_ntfy.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        first_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )
        target_id = target.id

        assert first_result.new_count == 1
        assert not first_result.baseline_mode
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test"]

    with SqliteApplicationContext(db_path) as app:
        reloaded_target = app.repositories.targets.get(target_id)
        assert reloaded_target is not None
        app.repositories.maintenance.clear_startup_runtime_data()

        assert app.repositories.scan_scope_state.is_initialized(reloaded_target.scope_id)

        clear_result = app.services.targets.reset_target_notification_state(reloaded_target.id)
        config = app.services.targets.get_config_for_target(reloaded_target)
        second_result = finalize_scan_items(
            app=app,
            target=reloaded_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:repeat",
                    alias_keys=("post:repeat",),
                    group_id="123",
                    text="票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert clear_result.notification_outbox_rows == 1
        assert clear_result.seen_items == 1
        assert clear_result.logical_seen_aliases == 1
        assert clear_result.dedupe_epoch_before == 0
        assert clear_result.dedupe_epoch_after == 1
        assert clear_result.scan_scope_initialized_before is True
        assert clear_result.scan_scope_initialized_after is True
        assert app.repositories.scan_scope_state.is_initialized(reloaded_target.scope_id)
        assert second_result.new_count == 1
        assert not second_result.baseline_mode
        assert (
            second_result.latest_items[0].debug_metadata["classification"]["eligible_for_notify"]
            is True
        )
        assert len(second_result.notification_payloads) == 1
        dispatch_pending_notifications_for_test(app=app, ntfy_sender=fake_ntfy_sender)

    assert sent_ntfy == ["phase0test", "phase0test"]


def test_empty_baseline_scan_does_not_initialize_scope(tmp_path: Path) -> None:
    """baseline 空結果不應解除抑制，避免下一輪通知既有項目。"""

    sent_ntfy: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_ntfy.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        app.repositories.scan_scope_state.clear_scope(target.scope_id)
        config = app.services.targets.get_config_for_target(target)

        empty_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[],
            item_count=0,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert empty_result.baseline_mode
        assert not app.repositories.scan_scope_state.is_initialized(target.scope_id)

        baseline_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:existing",
                    alias_keys=("post:existing",),
                    group_id="123",
                    text="既有票券貼文",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
        )

        assert baseline_result.baseline_mode
        assert (
            baseline_result.latest_items[0].debug_metadata["classification"]["eligible_for_notify"]
            is False
        )
        assert app.repositories.notification_outbox.list_pending() == []
        assert app.repositories.scan_scope_state.is_initialized(target.scope_id)

    assert sent_ntfy == []


def test_scan_failure_marks_profile_needs_login_and_success_clears_it(
    tmp_path: Path,
) -> None:
    """session failure 會觸發全域重新登入警告，下一次成功掃描會清除。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        target = _activate_target(app, target)
        record_scan_failure(
            app=app,
            target=target,
            reason="login_required",
            message="Facebook login is required.",
            worker_path="resident_main",
        )
        needs_login = app.repositories.app_settings.get_profile_session_status()

        finalize_scan_items(
            app=app,
            target=target,
            config=app.services.targets.get_config_for_target(target),
            items=[],
            item_count=0,
            metadata={"worker": "test_worker"},
        )
        ok_status = app.repositories.app_settings.get_profile_session_status()

    assert needs_login.state == ProfileSessionState.NEEDS_LOGIN
    assert needs_login.reason == "login_required"
    assert ok_status.state == ProfileSessionState.OK


def test_finalize_scan_items_uses_exclude_ignore_phrase_masking(tmp_path: Path) -> None:
    """shared finalize 使用 exclude_ignore_phrases 遮罩 exclude 判斷。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票",),
                    exclude_keywords=("收",),
                    exclude_ignore_phrases=("全收;回收",),
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

        result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="allowed",
                    alias_keys=("allowed",),
                    group_id="123",
                    text="全收優先，紙本票兩張",
                ),
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="blocked",
                    alias_keys=("blocked",),
                    group_id="123",
                    text="全收優先，但我也想收 6/6 內野票",
                ),
            ],
            item_count=2,
            metadata={},
        )
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)

    assert result.matched_count == 1
    assert [item.matched_keyword for item in latest_items] == ["票", ""]
