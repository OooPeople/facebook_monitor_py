"""Shared scan finalize tests。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any
from typing import cast


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpdateTargetConfigRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.outbox_dispatch_service import (
    dispatch_new_pending_notification_outbox,
)
from facebook_monitor.notifications.outbox_dispatch_service import (
    dispatch_new_pending_notification_outbox_for_db,
)
from facebook_monitor.notifications.outbox_dispatch_service import retry_failed_notification_outbox
from facebook_monitor.notifications.outbox_enqueue_service import (
    build_notification_idempotency_key,
)
from facebook_monitor.notifications.outbox_enqueue_service import (
    queue_match_notifications_after_commit,
)
from facebook_monitor.worker.scan_finalize import NormalizedScanItem

from tests.worker.scan_finalize_test_helpers import finalize_scan_items
from tests.worker.scan_finalize_test_helpers import _activate_target


def _dispatch_pending_for_db(
    db_path: Path,
    **kwargs: Any,
) -> int:
    """測試 helper：明確模擬 background dispatcher drain pending outbox。"""

    with SqliteApplicationContext(db_path) as app:
        return dispatch_new_pending_notification_outbox(app=app, **kwargs).dispatched_count


def test_queue_match_notifications_reports_no_entries_for_duplicate_dedupe(
    tmp_path: Path,
) -> None:
    """match notification dedupe 已存在時，不應建立新的 outbox row。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="phase0test"),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        logical_seen = app.repositories.logical_items.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="post:dedupe",
                item_kind=ItemKind.POST,
            ),
            item_keys=("post:dedupe",),
        )

        first_entries = queue_match_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            item_key="post:dedupe",
            logical_item_id=logical_seen.logical_item_id,
            author="作者",
            item_text="票券",
            permalink="https://www.facebook.com/groups/123/posts/1",
            matched_keyword="票券",
            item_kind=ItemKind.POST,
        )
        duplicate_entries = queue_match_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            item_key="post:dedupe-alias",
            logical_item_id=logical_seen.logical_item_id,
            author="作者",
            item_text="票券",
            permalink="https://www.facebook.com/groups/123/posts/1",
            matched_keyword="票券",
            item_kind=ItemKind.POST,
        )
        pending_outbox = app.repositories.notification_outbox.list_pending()
        dedupe_count = app.repositories.notification_outbox.connection.execute(
            "SELECT COUNT(*) FROM notification_dedupe WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert len(first_entries) == 1
    assert duplicate_entries == ()
    assert len(pending_outbox) == 1
    assert dedupe_count == 1


def test_finalize_does_not_send_notification_when_transaction_rolls_back(
    tmp_path: Path,
) -> None:
    """scan finalize 失敗 rollback 時，不得送出外部通知。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄是否真的送出通知。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    db_path = tmp_path / "app.db"
    try:
        with SqliteApplicationContext(db_path) as app:
            target = app.services.targets.upsert_group_posts_target(
                UpsertGroupPostsTargetRequest(
                    group_id="123",
                    canonical_url="https://www.facebook.com/groups/123",
                    config=TargetConfigPatch(
                        include_keywords=("票券",),
                        enable_ntfy=True,
                        ntfy_topic="phase0test",
                    ),
                )
            )
            target = _activate_target(app, target)
            config = app.services.targets.get_config_for_target(target)

            def fail_replace_for_target(
                _target_id: str,
                _items: Iterable[LatestScanItem],
            ) -> None:
                """模擬 latest scan 寫入失敗。"""

                raise RuntimeError("latest_write_failed")

            cast(
                Any, app.repositories.latest_scan_items
            ).replace_for_target = fail_replace_for_target
            finalize_scan_items(
                app=app,
                target=target,
                config=config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:rollback",
                        alias_keys=("post:rollback",),
                        group_id="123",
                        text="票券",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
            )
    except RuntimeError as exc:
        assert str(exc) == "latest_write_failed"

    assert sent_ntfy == []
    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is None


def test_finalize_rollback_keeps_committed_target_but_discards_scan_writes(
    tmp_path: Path,
) -> None:
    """已提交 target 不應因本輪 finalize rollback 被刪，但 scan 寫入需全數回復。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄是否真的送出通知。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)

    try:
        with SqliteApplicationContext(db_path) as app:

            def fail_replace_for_target(
                _target_id: str,
                _items: Iterable[LatestScanItem],
            ) -> None:
                """模擬 latest scan 寫入失敗。"""

                raise RuntimeError("latest_write_failed")

            cast(
                Any, app.repositories.latest_scan_items
            ).replace_for_target = fail_replace_for_target
            finalize_scan_items(
                app=app,
                target=target,
                config=config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:rollback",
                        alias_keys=("post:rollback",),
                        group_id="123",
                        text="票券",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
            )
    except RuntimeError as exc:
        assert str(exc) == "latest_write_failed"

    assert sent_ntfy == []
    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        assert app.repositories.match_history.list_by_target(target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(target.id) == []
        connection = app.repositories.runtime_states.connection
        table_counts = {
            table_name: connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()[0]
            for table_name in (
                "scan_runs",
                "seen_items",
                "logical_items",
                "logical_item_aliases",
                "notification_dedupe",
                "notification_outbox",
            )
        }
        assert table_counts == {
            "scan_runs": 0,
            "seen_items": 0,
            "logical_items": 0,
            "logical_item_aliases": 0,
            "notification_dedupe": 0,
            "notification_outbox": 0,
        }


def test_outbox_keeps_retryable_failed_notification_after_commit(
    tmp_path: Path,
) -> None:
    """DB commit 成功後 dispatcher 送失敗時，outbox 保留 failed 狀態供重試。"""

    def failing_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        """模擬外部通知 I/O 失敗。"""

        raise RuntimeError(f"ntfy_down: https://ntfy.sh/{config.topic}")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:failed",
                    alias_keys=("post:failed",),
                    group_id="123",
                    text="票券",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    _dispatch_pending_for_db(db_path, ntfy_sender=failing_ntfy_sender)

    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        outbox_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=stored_target.id,
                item_key="post:failed",
                channel=NotificationChannel.NTFY,
            )
        )
        assert outbox_entry is not None
        assert outbox_entry.status.value == "failed"
        assert outbox_entry.attempts == 1
        assert outbox_entry.last_error == "ntfy_dispatch_failed:RuntimeError"
        assert "phase0test" not in outbox_entry.last_error
        events = app.repositories.notification_events.list_by_target(stored_target.id)
        assert len(events) == 1
        assert events[0].status == NotificationStatus.FAILED
        assert events[0].message == "ntfy_dispatch_failed:RuntimeError"
        assert "phase0test" not in events[0].message


def test_outbox_after_commit_dispatch_runs_once_for_multiple_matches(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """同一輪多筆 match 只 wake 一次；dispatcher drain 時不倍增 attempts。"""

    calls: list[str] = []
    wake_calls: list[Path] = []

    def failing_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        """記錄 sender 被呼叫次數並模擬外部 I/O 失敗。"""

        calls.append(config.topic)
        raise RuntimeError("down")

    def fake_wake(db_path: Path) -> bool:
        wake_calls.append(db_path)
        return True

    monkeypatch.setattr(
        "facebook_monitor.notifications.outbox_enqueue_service.wake_notification_outbox_dispatcher_for_db",
        fake_wake,
    )

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:a",
                    alias_keys=("post:a",),
                    group_id="123",
                    text="票券 A",
                ),
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:b",
                    alias_keys=("post:b",),
                    group_id="123",
                    text="票券 B",
                ),
            ],
            item_count=2,
            metadata={"worker": "test_worker"},
        )

    assert wake_calls == [db_path]
    _dispatch_pending_for_db(db_path, ntfy_sender=failing_ntfy_sender)

    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        entries = [
            app.repositories.notification_outbox.get_by_idempotency_key(
                build_notification_idempotency_key(
                    target_id=stored_target.id,
                    item_key=item_key,
                    channel=NotificationChannel.NTFY,
                )
            )
            for item_key in ("post:a", "post:b")
        ]
        events = app.repositories.notification_events.list_by_target(stored_target.id)

    assert len(calls) == 2
    assert all(entry is not None for entry in entries)
    assert [entry.attempts for entry in entries if entry is not None] == [1, 1]
    assert [entry.status.value for entry in entries if entry is not None] == [
        "failed",
        "failed",
    ]
    assert len(events) == 2
    assert all(event.status == NotificationStatus.FAILED for event in events)


def test_outbox_failed_result_records_one_event_per_entry(
    tmp_path: Path,
) -> None:
    """sender 回傳 failed result 時，每筆 outbox 只產生一筆 failed event。"""

    calls: list[str] = []

    def failed_result_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        calls.append(config.topic)
        return NtfyResult(ok=False, status_code=500, message="failed_result")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:failed-a",
                    alias_keys=("post:failed-a",),
                    group_id="123",
                    text="票券 A",
                ),
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:failed-b",
                    alias_keys=("post:failed-b",),
                    group_id="123",
                    text="票券 B",
                ),
            ],
            item_count=2,
            metadata={"worker": "test_worker"},
        )

    _dispatch_pending_for_db(db_path, ntfy_sender=failed_result_sender)

    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        events = app.repositories.notification_events.list_by_target(stored_target.id)
        entries = [
            app.repositories.notification_outbox.get_by_idempotency_key(
                build_notification_idempotency_key(
                    target_id=stored_target.id,
                    item_key=item_key,
                    channel=NotificationChannel.NTFY,
                )
            )
            for item_key in ("post:failed-a", "post:failed-b")
        ]

    assert len(calls) == 2
    assert len(events) == 2
    assert all(event.status == NotificationStatus.FAILED for event in events)
    assert all(event.message == "failed_result" for event in events)
    assert all(entry is not None for entry in entries)
    assert [entry.last_error for entry in entries if entry is not None] == [
        "failed_result",
        "failed_result",
    ]


def test_failed_outbox_is_not_retried_by_new_match_commit(tmp_path: Path) -> None:
    """新 match commit 只送 pending，不會順手重試舊 failed outbox。"""

    calls: list[str] = []
    fail_first = True

    def sometimes_failing_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        """第一輪失敗，第二輪成功，用來驗證 failed 不會被自動重試。"""

        calls.append(config.topic)
        if fail_first:
            raise RuntimeError("first_down")
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:failed-before",
                    alias_keys=("post:failed-before",),
                    group_id="123",
                    text="票券",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    _dispatch_pending_for_db(db_path, ntfy_sender=sometimes_failing_sender)

    fail_first = False
    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        config = app.services.targets.get_config_for_target(stored_target)
        finalize_scan_items(
            app=app,
            target=stored_target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:new",
                    alias_keys=("post:new",),
                    group_id="123",
                    text="票券",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    _dispatch_pending_for_db(db_path, ntfy_sender=sometimes_failing_sender)

    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        failed_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=stored_target.id,
                item_key="post:failed-before",
                channel=NotificationChannel.NTFY,
            )
        )
        new_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=stored_target.id,
                item_key="post:new",
                channel=NotificationChannel.NTFY,
            )
        )

    assert len(calls) == 2
    assert failed_entry is not None
    assert failed_entry.status.value == "failed"
    assert failed_entry.attempts == 1
    assert new_entry is not None
    assert new_entry.status.value == "sent"
    assert new_entry.attempts == 1


def test_failed_outbox_retry_requires_explicit_retry_api(tmp_path: Path) -> None:
    """failed outbox 只有明確呼叫 retry API 才會重試。"""

    calls: list[str] = []
    fail_first = True

    def sometimes_failing_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        calls.append(config.topic)
        if fail_first:
            raise RuntimeError("first_down")
        return NtfyResult(ok=True, status_code=200, message="retry_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:retry",
                    alias_keys=("post:retry",),
                    group_id="123",
                    text="票券",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    _dispatch_pending_for_db(db_path, ntfy_sender=sometimes_failing_sender)

    fail_first = False
    with SqliteApplicationContext(db_path) as app:
        retry_failed_notification_outbox(app=app, ntfy_sender=sometimes_failing_sender)

    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=stored_target.id,
                item_key="post:retry",
                channel=NotificationChannel.NTFY,
            )
        )

    assert len(calls) == 2
    assert entry is not None
    assert entry.status.value == "sent"
    assert entry.attempts == 2
    assert entry.last_error == ""


def test_failed_outbox_retry_resends_persisted_multiline_message(
    tmp_path: Path,
) -> None:
    """failed retry 重送 outbox 保存的多行訊息，不重新組裝內容。"""

    fail_first = True
    sent_messages: list[str] = []

    def sometimes_failing_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """第一輪失敗，retry 時記錄保存的 outbox message。"""

        if fail_first:
            raise RuntimeError("first_down")
        sent_messages.append(message)
        return NtfyResult(ok=True, status_code=200, message="retry_sent")

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
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:retry-multiline",
                    alias_keys=("post:retry-multiline",),
                    group_id="123",
                    text="第一行票券 第二行座位",
                    display_text="第一行票券\n第二行座位",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    _dispatch_pending_for_db(db_path, ntfy_sender=sometimes_failing_sender)

    fail_first = False
    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        entry_before_retry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=stored_target.id,
                item_key="post:retry-multiline",
                channel=NotificationChannel.NTFY,
            )
        )
        assert entry_before_retry is not None
        stored_message = entry_before_retry.message

        retry_failed_notification_outbox(app=app, ntfy_sender=sometimes_failing_sender)

    assert sent_messages == [stored_message]
    assert (
        "命中：票券\n---------------------------------------------\n第一行票券\n第二行座位"
    ) in stored_message


def test_discord_outbox_uses_display_text_newlines(tmp_path: Path) -> None:
    """Discord outbox message 使用 display text 的換行格式。"""

    sent_discord: list[tuple[DiscordConfig, str, str]] = []

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord payload，避免測試送出真實 webhook。"""

        sent_discord.append((config, title, message))
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/1234567890/token",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:discord-multiline",
                    alias_keys=("post:discord-multiline",),
                    group_id="123",
                    text="第一行票券 第二行座位",
                    display_text="第一行票券\n第二行座位",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )
        entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=target.id,
                item_key="post:discord-multiline",
                channel=NotificationChannel.DISCORD,
            )
        )
        assert entry is not None
        assert "命中：票券\n---------------------------------------------\n第一行票券" in (
            entry.message
        )
        assert "第一行票券\n第二行座位" in entry.message
        assert "**票券**" not in entry.message
        assert "內容:" not in entry.message
        assert "命中:" not in entry.message
        assert "```" not in entry.message
        assert entry.message.endswith("\n第二行座位")
        assert "\x1b" not in entry.message
        assert "━" not in entry.message

    _dispatch_pending_for_db(db_path, discord_sender=fake_discord_sender)

    assert len(sent_discord) == 1
    assert (
        "命中：票券\n---------------------------------------------\n第一行票券"
        in (sent_discord[0][2])
    )
    assert "第一行票券\n第二行座位" in sent_discord[0][2]
    assert "**票券**" not in sent_discord[0][2]
    assert "內容:" not in sent_discord[0][2]
    assert "命中:" not in sent_discord[0][2]
    assert "```" not in sent_discord[0][2]
    assert sent_discord[0][2].endswith("\n第二行座位")
    assert "\x1b" not in sent_discord[0][2]
    assert "━" not in sent_discord[0][2]


def test_discord_outbox_persists_permalink_separator(tmp_path: Path) -> None:
    """Discord outbox 有 permalink 時保存內容下方分隔線與 angle-wrapped URL。"""

    sent_discord: list[tuple[DiscordConfig, str, str]] = []

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord payload，避免測試送出真實 webhook。"""

        sent_discord.append((config, title, message))
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    db_path = tmp_path / "app.db"
    permalink = "https://www.facebook.com/groups/123/posts/1"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/1234567890/token",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:discord-permalink",
                    alias_keys=("post:discord-permalink",),
                    group_id="123",
                    text="第一行票券",
                    display_text="第一行票券",
                    permalink=permalink,
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )
        entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=target.id,
                item_key="post:discord-permalink",
                channel=NotificationChannel.DISCORD,
            )
        )
        assert entry is not None
        assert (
            "第一行票券\n---------------------------------------------\n"
            "<https://www.facebook.com/groups/123/posts/1>"
        ) in entry.message
        assert entry.message.endswith("<https://www.facebook.com/groups/123/posts/1>")

    _dispatch_pending_for_db(db_path, discord_sender=fake_discord_sender)

    assert len(sent_discord) == 1
    assert sent_discord[0][2] == entry.message


def test_outbox_dispatch_releases_processing_heartbeat_before_external_io(
    tmp_path: Path,
) -> None:
    """outbox dispatch 不應在外部通知 I/O 期間持有 SQLite write transaction。"""

    db_path = tmp_path / "app.db"
    in_transaction_during_send: list[bool] = []

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:post:io-lock:ntfy",
                target_id=target.id,
                item_key="post:io-lock",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
                endpoint="phase0test",
            )
        )

        def fake_ntfy_sender(
            config: NtfyConfig,
            _title: str,
            _message: str,
        ) -> NtfyResult:
            """記錄 sender 執行時是否仍持有 write transaction。"""

            in_transaction_during_send.append(
                app.repositories.notification_outbox.connection.in_transaction
            )
            return NtfyResult(ok=True, status_code=200, message="sent")

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )

    assert result.dispatched_count == 1
    assert in_transaction_during_send == [False]


def test_outbox_dispatch_refreshes_polluted_match_group_line(
    tmp_path: Path,
) -> None:
    """pending match outbox 投遞前會用目前 target 顯示名稱修正舊社團欄位。"""

    db_path = tmp_path / "app.db"
    sent_messages: list[str] = []

    def fake_ntfy_sender(
        _config: NtfyConfig,
        _title: str,
        message: str,
    ) -> NtfyResult:
        """記錄實際送出的 ntfy 本文。"""

        sent_messages.append(message)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:post:old-name:ntfy",
                target_id=target.id,
                item_key="post:old-name",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="社團：Facebook | Error\n本文",
                endpoint="phase0test",
            )
        )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )

    assert result.dispatched_count == 1
    assert sent_messages == ["社團：測試社團\n本文"]


def test_outbox_dispatch_refreshes_only_first_match_group_header_with_channel_format(
    tmp_path: Path,
) -> None:
    """舊 match outbox 只修 metadata header，且套用實際通道格式。"""

    db_path = tmp_path / "app.db"
    sent_messages: list[str] = []

    def fake_discord_sender(
        _config: DiscordConfig,
        _title: str,
        message: str,
    ) -> DiscordResult:
        """記錄實際送出的 Discord 本文。"""

        sent_messages.append(message)
        return DiscordResult(ok=True, status_code=204, message="discord_sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    enable_discord_notification=True,
                    discord_webhook="https://discord.com/api/webhooks/1234567890/token",
                ),
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="我的 <測試>\n名稱",
                group_name="測試社團",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:post:discord-old-name:discord",
                target_id=target.id,
                item_key="post:discord-old-name",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DISCORD,
                title="title",
                message="社團：Facebook | Error\n社團：正文不要改",
                endpoint="https://discord.com/api/webhooks/1234567890/token",
            )
        )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            discord_sender=fake_discord_sender,
        )

    assert result.dispatched_count == 1
    assert sent_messages == ["社團：我的 \\<測試\\> 名稱\n社團：正文不要改"]


def test_outbox_dispatch_refreshes_polluted_runtime_failure_target_line(
    tmp_path: Path,
) -> None:
    """舊 runtime failure pending row 投遞前也會修正污染 target 名稱。"""

    db_path = tmp_path / "app.db"
    sent_messages: list[str] = []

    def fake_ntfy_sender(
        _config: NtfyConfig,
        _title: str,
        message: str,
    ) -> NtfyResult:
        """記錄實際送出的 ntfy 本文。"""

        sent_messages.append(message)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:runtime-failure:terminal:ntfy",
                target_id=target.id,
                item_key="runtime-failure:terminal",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="掃描狀態發生錯誤",
                message=("監視項目: Facebook | Error | 錯誤類型: 未分類錯誤 | 連續次數: 3"),
                endpoint="runtime-test",
                event_kind=NotificationEventKind.RUNTIME_FAILURE,
                source_scan_run_id=130,
                failure_reason=UNKNOWN_REASON,
                failure_count=3,
            )
        )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )

    assert result.dispatched_count == 1
    assert sent_messages == ["監視項目: 測試社團 | 錯誤類型: 未分類錯誤 | 連續次數: 3"]


def test_outbox_dispatch_skips_preterminal_runtime_failure_pending_row(
    tmp_path: Path,
) -> None:
    """升級前殘留的 pre-terminal runtime failure pending row 不應被送出。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="legacy-runtime-unknown",
                canonical_url="https://www.facebook.com/groups/legacy-runtime-unknown",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        idempotency_key = build_notification_idempotency_key(
            target_id=target.id,
            item_key="runtime-failure:legacy",
            channel=NotificationChannel.NTFY,
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=idempotency_key,
                target_id=target.id,
                item_key="runtime-failure:legacy",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="掃描狀態發生錯誤",
                message="連續次數: 1",
                endpoint="runtime-test",
                event_kind=NotificationEventKind.RUNTIME_FAILURE,
                source_scan_run_id=128,
                failure_reason=UNKNOWN_REASON,
                failure_count=1,
            )
        )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        entry = app.repositories.notification_outbox.get_by_idempotency_key(idempotency_key)
        event = app.repositories.notification_events.latest_by_target(target.id)

    assert result.dispatched_count == 1
    assert sent_topics == []
    assert entry is not None
    assert entry.status == NotificationOutboxStatus.SKIPPED
    assert entry.attempts == 1
    assert entry.last_error == "runtime_failure_not_terminal"
    assert event is not None
    assert event.status == NotificationStatus.SKIPPED
    assert event.event_kind == NotificationEventKind.RUNTIME_FAILURE
    assert event.failure_reason == UNKNOWN_REASON
    assert event.failure_count == 1


def test_outbox_dispatch_skips_scheduler_stopping_runtime_failure_pending_row(
    tmp_path: Path,
) -> None:
    """scheduler shutdown/cancel 類 runtime failure pending row 不應被送出。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="legacy-runtime-stopping",
                canonical_url="https://www.facebook.com/groups/legacy-runtime-stopping",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        idempotency_key = build_notification_idempotency_key(
            target_id=target.id,
            item_key="runtime-failure:scheduler-stopping",
            channel=NotificationChannel.NTFY,
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=idempotency_key,
                target_id=target.id,
                item_key="runtime-failure:scheduler-stopping",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="掃描狀態發生錯誤",
                message="連續次數: 3",
                endpoint="runtime-test",
                event_kind=NotificationEventKind.RUNTIME_FAILURE,
                source_scan_run_id=129,
                failure_reason=SCHEDULER_STOPPING_REASON,
                failure_count=3,
            )
        )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        entry = app.repositories.notification_outbox.get_by_idempotency_key(idempotency_key)
        event = app.repositories.notification_events.latest_by_target(target.id)

    assert result.dispatched_count == 1
    assert sent_topics == []
    assert entry is not None
    assert entry.status == NotificationOutboxStatus.SKIPPED
    assert event is not None
    assert event.status == NotificationStatus.SKIPPED
    assert event.failure_reason == SCHEDULER_STOPPING_REASON
    assert event.failure_count == 3


def test_stale_failed_retry_processing_does_not_become_pending_dispatch(
    tmp_path: Path,
) -> None:
    """failed retry claim 崩潰後只回到 failed，不會被一般 pending dispatch 重送。"""

    calls: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        calls.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=build_notification_idempotency_key(
                    target_id=target.id,
                    item_key="post:failed-retry",
                    channel=NotificationChannel.NTFY,
                ),
                target_id=target.id,
                item_key="post:failed-retry",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
                endpoint="phase0test",
                status=NotificationOutboxStatus.FAILED,
                attempts=1,
                last_error="previous_down",
            )
        )

    with SqliteApplicationContext(db_path) as app:
        claimed = app.repositories.notification_outbox.claim_failed()
        assert len(claimed) == 1
        assert claimed[0].entry.status == NotificationOutboxStatus.PROCESSING_FAILED
        app.repositories.notification_outbox.connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (claimed[0].entry.id,),
        )

    with SqliteApplicationContext(db_path) as app:
        recovered_count = app.repositories.notification_outbox.recover_stale_processing(
            older_than_seconds=60
        )
        dispatch_new_pending_notification_outbox(app=app, ntfy_sender=fake_ntfy_sender)
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=stored_target.id,
                item_key="post:failed-retry",
                channel=NotificationChannel.NTFY,
            )
        )

    assert recovered_count == 1
    assert calls == []
    assert entry is not None
    assert entry.status == NotificationOutboxStatus.FAILED
    assert entry.attempts == 1


def test_outbox_dispatch_is_idempotent_for_sent_event(tmp_path: Path) -> None:
    """同一 outbox event 已 sent 後，重跑 dispatcher 不會重複送通知。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄通知發送次數。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keywords=("票券",),
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                ),
            )
        )
        target = _activate_target(app, target)
        config = app.services.targets.get_config_for_target(target)
        finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:idempotent",
                    alias_keys=("post:idempotent",),
                    group_id="123",
                    text="票券",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
        )

    with SqliteApplicationContext(db_path) as app:
        dispatch_new_pending_notification_outbox(app=app, ntfy_sender=fake_ntfy_sender)

    assert len(sent_ntfy) == 1


def test_outbox_pending_dispatch_drains_all_default_batches(tmp_path: Path) -> None:
    """pending dispatch 會分批 claim 直到本輪 pending outbox 清空。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="topic"),
            )
        )
        for index in range(11):
            app.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:item-{index}:ntfy",
                    target_id=target.id,
                    item_key=f"item-{index}",
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                    endpoint="topic",
                )
            )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        pending = app.repositories.notification_outbox.list_pending(limit=20)

    assert result.dispatched_count == 11
    assert len(sent_topics) == 11
    assert len(pending) == 0


def test_outbox_pending_dispatch_stops_before_claiming_next_batch(
    tmp_path: Path,
) -> None:
    """stop predicate 變 true 後，pending dispatch 不得 claim 下一批。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="topic"),
            )
        )
        for index in range(11):
            app.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:stop-item-{index}:ntfy",
                    target_id=target.id,
                    item_key=f"stop-item-{index}",
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                    endpoint="topic",
                )
            )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
            batch_limit=5,
            should_stop=lambda: len(sent_topics) >= 5,
        )
        pending = app.repositories.notification_outbox.list_pending(limit=20)

    assert result.dispatched_count == 5
    assert len(sent_topics) == 5
    assert len(pending) == 6


def test_outbox_pending_dispatch_respects_max_batches(
    tmp_path: Path,
) -> None:
    """max_batches 限制本輪 dispatch 最多 claim 幾個 pending batch。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="topic"),
            )
        )
        for index in range(11):
            app.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:bounded-item-{index}:ntfy",
                    target_id=target.id,
                    item_key=f"bounded-item-{index}",
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                    endpoint="topic",
                )
            )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
            batch_limit=5,
            max_batches=1,
        )
        pending = app.repositories.notification_outbox.list_pending(limit=20)

    assert result.dispatched_count == 5
    assert len(sent_topics) == 5
    assert len(pending) == 6


def test_outbox_pending_dispatch_result_counts_claimed_failed_batch(
    tmp_path: Path,
) -> None:
    """sender 例外時，bounded drain result 仍需回報 claimed batch 容量。"""

    db_path = tmp_path / "app.db"

    def failing_ntfy_sender(
        _config: NtfyConfig,
        _title: str,
        _message: str,
    ) -> NtfyResult:
        raise RuntimeError("ntfy unavailable")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="topic"),
            )
        )
        for index in range(11):
            app.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:failed-batch-item-{index}:ntfy",
                    target_id=target.id,
                    item_key=f"failed-batch-item-{index}",
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                    endpoint="topic",
                )
            )

        result = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=failing_ntfy_sender,
            batch_limit=5,
            max_batches=1,
        )
        pending = app.repositories.notification_outbox.list_pending(limit=20)
        failed_count = app.repositories.notification_outbox.connection.execute(
            """
            SELECT COUNT(*)
            FROM notification_outbox
            WHERE status = ?
            """,
            (NotificationOutboxStatus.FAILED.value,),
        ).fetchone()[0]

    assert result.dispatched_count == 0
    assert result.claimed_count == 5
    assert result.batch_count == 1
    assert result.reached_batch_limit
    assert result.should_continue
    assert len(pending) == 6
    assert failed_count == 5


def test_outbox_pending_dispatch_for_db_returns_typed_result(
    tmp_path: Path,
) -> None:
    """DB wrapper 應保留 pending dispatch 的 typed drain result。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="topic"),
            )
        )
        for index in range(3):
            app.repositories.notification_outbox.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:for-db-item-{index}:ntfy",
                    target_id=target.id,
                    item_key=f"for-db-item-{index}",
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title="title",
                    message="message",
                    endpoint="topic",
                )
            )

    result = dispatch_new_pending_notification_outbox_for_db(
        db_path=db_path,
        ntfy_sender=fake_ntfy_sender,
        batch_limit=2,
        max_batches=1,
    )

    assert result.dispatched_count == 2
    assert result.claimed_count == 2
    assert result.batch_count == 1
    assert result.reached_batch_limit
    assert result.should_continue
    assert sent_topics == ["topic", "topic"]


def test_failed_outbox_retry_refreshes_current_target_endpoint(tmp_path: Path) -> None:
    """failed retry 會套用目前 target config，避免重打舊 endpoint。"""

    db_path = tmp_path / "app.db"
    sent_topics: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_topics.append(config.topic)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                config=TargetConfigPatch(enable_ntfy=True, ntfy_topic="old-topic"),
            )
        )
        entry = app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-1:ntfy",
                target_id=target.id,
                item_key="item-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
                endpoint="old-topic",
            )
        ).entry
        claimed = app.repositories.notification_outbox.claim_pending()[0]
        assert app.repositories.notification_outbox.mark_result(
            entry_id=entry.id or 0,
            status=NotificationOutboxStatus.FAILED,
            attempts=1,
            processing_status=claimed.entry.status,
            claim_token=claimed.claim_token,
            message="first_down",
        )
        app.services.targets.update_target_config(
            UpdateTargetConfigRequest(
                target_id=target.id,
                config=TargetConfigPatch(ntfy_topic="new-topic"),
            )
        )

        sent_count = retry_failed_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        updated = app.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:item-1:ntfy"
        )

    assert sent_count == 1
    assert sent_topics == ["new-topic"]
    assert updated is not None
    assert updated.endpoint == "new-topic"
    assert updated.status == NotificationOutboxStatus.SENT
    assert updated.last_error == ""
