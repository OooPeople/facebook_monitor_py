"""Shared scan finalize tests。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from typing import cast

import pytest

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import TargetConfigPatch
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationDedupeStatus
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import TARGET_PAGE_RESTART_ACTION
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.keyword_groups import keyword_group_slots
from facebook_monitor.notifications.outbox_service import enqueue_runtime_failure_notifications
from facebook_monitor.persistence.repositories.app_settings import ProfileSessionState
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.notifications.desktop import DesktopNotificationResult
from facebook_monitor.notifications.discord import DiscordConfig
from facebook_monitor.notifications.discord import DiscordResult
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.outbox_service import build_notification_idempotency_key
from facebook_monitor.notifications.outbox_service import dispatch_new_pending_notification_outbox
from facebook_monitor.notifications.outbox_service import retry_failed_notification_outbox
from facebook_monitor.notifications.outbox_service import (
    queue_runtime_failure_notifications_after_commit,
)
from facebook_monitor.worker import scan_failure_finalize as scan_failure_finalize_module
from facebook_monitor.worker.scan_finalize import NormalizedScanItem
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import UNGUARDED_SCAN_COMMIT
from facebook_monitor.worker.scan_finalize import finalize_scan_items as _finalize_scan_items
from facebook_monitor.worker.scan_finalize import record_skipped_scan as _record_skipped_scan
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure
from facebook_monitor.worker.scan_failure_finalize import record_guarded_scan_failure
from facebook_monitor.worker.errors import WorkerFailure


def finalize_scan_items(**kwargs: Any) -> Any:
    """測試預設走明確 unguarded finalize；guard 案例可覆寫 commit_guard。"""

    kwargs.setdefault("commit_guard", UNGUARDED_SCAN_COMMIT)
    return _finalize_scan_items(**kwargs)


def record_skipped_scan(**kwargs: Any) -> Any:
    """測試預設走明確 unguarded skip finalize；guard 案例可覆寫 commit_guard。"""

    kwargs.setdefault("commit_guard", UNGUARDED_SCAN_COMMIT)
    return _record_skipped_scan(**kwargs)


def _activate_target(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> TargetDescriptor:
    """讓 finalize 測試明確模擬正式 worker 正在處理 active target。"""

    activated = app.services.targets.restart_target_monitoring(target.id)
    app.repositories.scan_scope_state.mark_initialized(activated.scope_id)
    return activated


@dataclass(frozen=True)
class RunningTargetFixture:
    """保存已取得 scan admission 的 target 測試資料。"""

    target: TargetDescriptor
    config: TargetConfig
    commit_guard: ScanCommitGuard


def _stub_outbox_dispatch(monkeypatch: Any) -> list[Path]:
    """攔截 after-commit outbox dispatch，避免測試打到外部通知服務。"""

    dispatch_calls: list[Path] = []

    def fake_dispatch(**kwargs: object) -> int:
        db_path = kwargs["db_path"]
        assert isinstance(db_path, Path)
        dispatch_calls.append(db_path)
        return 1

    monkeypatch.setattr(
        "facebook_monitor.notifications.outbox_service.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )
    return dispatch_calls


def _create_running_target_with_guard(
    app: ApplicationContext,
    *,
    include_keywords: tuple[str, ...] = (),
) -> RunningTargetFixture:
    """建立 active target、初始化 scope，並回傳目前 running attempt guard。"""

    target = app.services.targets.upsert_group_posts_target(
        UpsertGroupPostsTargetRequest(
            group_id="123",
            canonical_url="https://www.facebook.com/groups/123",
            group_name="測試社團",
            config=TargetConfigPatch(include_keywords=include_keywords),
        )
    )
    target = _activate_target(app, target)
    config = app.services.targets.get_config_for_target(target)
    app.repositories.scan_scope_state.mark_initialized(target.scope_id)
    running_state = app.services.targets.mark_target_running(
        target.id,
        "worker-a",
        page_id="page-a",
    )
    return RunningTargetFixture(
        target=target,
        config=config,
        commit_guard=scan_commit_guard_from_runtime_state(running_state),
    )


def test_finalize_scan_items_records_shared_postprocess_state(tmp_path: Path) -> None:
    """shared finalize 會集中寫入 seen/history/latest scan 與通知事件。"""

    sent_ntfy: list[tuple[NtfyConfig, str, str]] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        """記錄 ntfy payload，避免測試送出真實通知。"""

        sent_ntfy.append((config, title, message))
        return NtfyResult(ok=True, status_code=200, message="ntfy_sent")

    def fake_desktop_sender(title: str, message: str) -> DesktopNotificationResult:
        """記錄桌面通知成功結果。"""

        return DesktopNotificationResult(ok=True, status_code=None, message="desktop_sent")

    def fake_discord_sender(
        config: DiscordConfig,
        title: str,
        message: str,
    ) -> DiscordResult:
        """記錄 Discord 通知成功結果。"""

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
                    enable_ntfy=True,
                    ntfy_topic="phase0test",
                    enable_desktop_notification=True,
                    enable_discord_notification=True,
                    discord_webhook="https://discord.example/webhook",
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
                    item_key="post:1",
                    alias_keys=("post:1", "legacy:1"),
                    group_id="123",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink="https://www.facebook.com/groups/123/posts/1",
                    raw_target_kind="posts",
                    metadata={"source": "test"},
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
            desktop_notification_sender=fake_desktop_sender,
            discord_notification_sender=fake_discord_sender,
        )

        assert result.new_count == 1
        assert result.matched_count == 1
        assert result.scan_run_id > 0
        assert result.latest_items[0].matched_keyword == "票券"
        assert result.latest_items[0].debug_metadata["source"] == "test"
        assert result.latest_items[0].debug_metadata["classification"] == {
            "is_new": True,
            "is_matched": True,
            "include_rule": "票券",
            "include_rules": ["票券"],
            "include_group_results": [
                {
                    "group_id": "1",
                    "group_label": "關鍵字 1",
                    "matched": True,
                    "rules": ["票券"],
                }
            ],
            "exclude_rule": "",
            "eligible_for_notify": True,
            "baseline_mode": False,
        }
        history = app.repositories.match_history.list_by_target(target.id)
        assert len(history) == 1
        assert history[0].include_rule == "票券"
        latest_items = app.repositories.latest_scan_items.list_by_target(target.id)
        assert len(latest_items) == 1
        outbox_entries = app.repositories.notification_outbox.list_pending()
        assert {entry.channel for entry in outbox_entries} == {
            NotificationChannel.DESKTOP,
            NotificationChannel.NTFY,
            NotificationChannel.DISCORD,
        }
        assert app.repositories.notification_events.list_by_target(target.id) == []

        second_result = finalize_scan_items(
            app=app,
            target=target,
            config=config,
            items=[
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:1",
                    alias_keys=("post:1", "legacy:1"),
                    group_id="123",
                    author="作者",
                    text="這是一篇票券貼文",
                    permalink="https://www.facebook.com/groups/123/posts/1",
                    raw_target_kind="posts",
                )
            ],
            item_count=1,
            metadata={"worker": "test_worker"},
            notification_sender=fake_ntfy_sender,
            desktop_notification_sender=fake_desktop_sender,
            discord_notification_sender=fake_discord_sender,
        )

        assert second_result.new_count == 0
        assert second_result.matched_count == 1
        assert len(app.repositories.match_history.list_by_target(target.id)) == 1

    assert sent_ntfy
    with SqliteApplicationContext(db_path) as app:
        events = app.repositories.notification_events.list_by_target(target.id)
        assert {event.channel for event in events} == {
            NotificationChannel.DESKTOP,
            NotificationChannel.NTFY,
            NotificationChannel.DISCORD,
        }
        assert all(event.status == NotificationStatus.SENT for event in events)
        assert app.repositories.notification_outbox.list_pending() == []


def test_finalize_scan_items_requires_all_include_keyword_groups(tmp_path: Path) -> None:
    """shared finalize 使用 include groups 判斷命中並保存 group 診斷。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                config=TargetConfigPatch(
                    include_keyword_groups=keyword_group_slots(
                        (("5/1;5/2",), ("108;109",))
                    ),
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
                    item_key="post:1",
                    alias_keys=("post:1",),
                    group_id="123",
                    text="售 5/2 109 區票券",
                    raw_target_kind="posts",
                ),
                NormalizedScanItem(
                    item_kind=ItemKind.POST,
                    item_key="post:2",
                    alias_keys=("post:2",),
                    group_id="123",
                    text="售 5/2 票券",
                    raw_target_kind="posts",
                ),
            ],
            item_count=2,
            metadata={"worker": "test_worker"},
        )

        assert result.matched_count == 1
        assert result.latest_items[0].matched_keyword == "5/2;109"
        assert [
            (match.group_id, match.group_label, match.rule)
            for match in result.latest_items[0].matched_keyword_groups
        ] == [("1", "關鍵字 1", "5/2"), ("2", "關鍵字 2", "109")]
        assert result.latest_items[1].matched_keyword == ""
        assert result.latest_items[1].debug_metadata["classification"][
            "include_group_results"
        ] == [
            {
                "group_id": "1",
                "group_label": "關鍵字 1",
                "matched": True,
                "rules": ["5/2"],
            },
            {
                "group_id": "2",
                "group_label": "關鍵字 2",
                "matched": False,
                "rules": [],
            },
        ]


def test_finalize_scan_items_refuses_stopped_target_commit(tmp_path: Path) -> None:
    """target 停止後才完成的掃描不得寫入 seen/history/latest/notification。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        config = app.services.targets.get_config_for_target(target)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)
        target = app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.pause_target_monitoring(target.id)

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=target,
                config=config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:1",
                        alias_keys=("post:1",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(target.id) == []
        assert app.repositories.notification_outbox.list_pending(limit=10) == []


def test_finalize_scan_items_refuses_paused_descriptor_commit(tmp_path: Path) -> None:
    """即使呼叫端傳入的是 paused descriptor，finalize 仍不得寫入結果。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
                group_name="測試社團",
                config=TargetConfigPatch(include_keywords=("票券",)),
            )
        )
        config = app.services.targets.get_config_for_target(target)
        app.repositories.scan_scope_state.mark_initialized(target.scope_id)

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=target,
                config=config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:1",
                        alias_keys=("post:1",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1234567890",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(target.id) == []
        assert app.repositories.notification_outbox.list_pending(limit=10) == []


def test_finalize_scan_items_refuses_restarted_attempt_commit(tmp_path: Path) -> None:
    """target stop/start 後，舊掃描 attempt 不得再寫入結果。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(
            app,
            include_keywords=("票券",),
        )
        app.services.targets.pause_target_monitoring(fixture.target.id)
        app.services.targets.restart_target_monitoring(fixture.target.id)

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=fixture.target,
                config=fixture.config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:restart-race",
                        alias_keys=("post:restart-race",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1234567890",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
                commit_guard=fixture.commit_guard,
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(fixture.target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(fixture.target.id) == []
        assert app.repositories.notification_outbox.list_pending(limit=10) == []


def test_record_skipped_scan_refuses_restarted_attempt_commit(tmp_path: Path) -> None:
    """sort-adjust skip 也不得在 stop/start 後清掉新一輪 latest snapshot。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.repositories.latest_scan_items.replace_for_target(
            fixture.target.id,
            [
                LatestScanItem(
                    target_id=fixture.target.id,
                    scan_run_id=99,
                    item_kind=ItemKind.POST,
                    item_key="previous",
                    item_index=0,
                    text="新一輪已存在的 snapshot",
                )
            ],
        )
        app.services.targets.pause_target_monitoring(fixture.target.id)
        app.services.targets.restart_target_monitoring(fixture.target.id)

        with pytest.raises(WorkerFailure) as excinfo:
            record_skipped_scan(
                app=app,
                target=fixture.target,
                metadata={"worker": "test_worker"},
                commit_guard=fixture.commit_guard,
            )

        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        assert excinfo.value.reason == "target_stopped"
        assert len(latest_items) == 1
        assert latest_items[0].item_key == "previous"


def test_finalize_scan_items_refuses_reused_worker_with_different_page(
    tmp_path: Path,
) -> None:
    """同 worker / started_at 但 page identity 已換掉時，舊頁面不得寫回。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(
            app,
            include_keywords=("票券",),
        )
        runtime_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(
            replace(runtime_state, active_page_id="page-b")
        )

        with pytest.raises(WorkerFailure) as excinfo:
            finalize_scan_items(
                app=app,
                target=fixture.target,
                config=fixture.config,
                items=[
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:page-drift",
                        alias_keys=("post:page-drift",),
                        group_id="123",
                        author="作者",
                        text="票券",
                        permalink="https://www.facebook.com/groups/123/posts/1234567890",
                    )
                ],
                item_count=1,
                metadata={"worker": "test_worker"},
                commit_guard=fixture.commit_guard,
            )

        assert excinfo.value.reason == "target_stopped"
        assert app.repositories.match_history.list_by_target(fixture.target.id) == []
        assert app.repositories.latest_scan_items.list_by_target(fixture.target.id) == []


def test_finalize_scan_items_starts_write_transaction_before_first_scan_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """guard check 與 scan 結果寫入必須包在同一個 SQLite write transaction。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    saw_write_transaction: list[bool] = []
    with SqliteApplicationContext(db_path) as app:
        original_mark_profile_ok = app.repositories.app_settings.mark_profile_ok

        def mark_profile_ok_with_assertion(*, source: str) -> None:
            """記錄第一個 scan finalize 寫入前是否已持有 transaction。"""

            saw_write_transaction.append(
                app.repositories.runtime_states.connection.in_transaction
            )
            original_mark_profile_ok(source=source)

        monkeypatch.setattr(
            app.repositories.app_settings,
            "mark_profile_ok",
            mark_profile_ok_with_assertion,
        )
        finalize_scan_items(
            app=app,
            target=fixture.target,
            config=fixture.config,
            items=[],
            item_count=0,
            metadata={"worker": "test_worker"},
            commit_guard=fixture.commit_guard,
        )

    assert saw_write_transaction == [True]


def test_record_guarded_scan_failure_starts_write_transaction_before_failure_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure finalize 的 guard check 與 failure scan run 寫入也要同 transaction。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    saw_write_transaction: list[bool] = []
    with SqliteApplicationContext(db_path) as app:

        def record_scan_failure_with_assertion(**kwargs: object) -> int:
            """記錄 failure scan run 寫入前是否已持有 transaction。"""

            saw_write_transaction.append(
                app.repositories.runtime_states.connection.in_transaction
            )
            return record_scan_failure(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            scan_failure_finalize_module,
            "record_scan_failure",
            record_scan_failure_with_assertion,
        )
        decision = record_guarded_scan_failure(
            app=app,
            target_id=fixture.target.id,
            reason="unknown",
            message="boom",
            source="worker_failure",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
        )

    assert decision is not None
    assert saw_write_transaction == [True]


def test_active_targets_runtime_failure_notifies_after_retry_limit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """resident 全域錯誤前兩次只重試，第三次才通知並停止 target。"""

    db_path = tmp_path / "app.db"
    dispatch_calls = _stub_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
                group_name="Active target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        stopped = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="stopped",
                canonical_url="https://www.facebook.com/groups/stopped",
                group_name="Stopped target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        paused = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="paused",
                canonical_url="https://www.facebook.com/groups/paused",
                group_name="Paused target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        errored = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="errored",
                canonical_url="https://www.facebook.com/groups/errored",
                group_name="Errored target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.services.targets.restart_target_monitoring(active.id)
        app.services.targets.restart_target_monitoring(paused.id)
        app.services.targets.pause_target_monitoring(paused.id)
        app.services.targets.restart_target_monitoring(errored.id)
        app.services.targets.mark_target_error(errored.id, "existing terminal error")
        first_count = (
            scan_failure_finalize_module.record_active_targets_runtime_failure_notifications(
                app=app,
                reason=SCHEDULER_RUNTIME_REASON,
                message="Target page, context or browser has been closed",
                worker_path="resident_scheduler",
                exception_class="RuntimeError",
            )
        )
        first_state = app.repositories.runtime_states.get(active.id)
        first_entries = app.repositories.notification_outbox.list_pending()

        second_count = (
            scan_failure_finalize_module.record_active_targets_runtime_failure_notifications(
                app=app,
                reason=SCHEDULER_RUNTIME_REASON,
                message="Target page, context or browser has been closed",
                worker_path="resident_scheduler",
                exception_class="RuntimeError",
            )
        )
        second_state = app.repositories.runtime_states.get(active.id)
        second_entries = app.repositories.notification_outbox.list_pending()

        third_count = (
            scan_failure_finalize_module.record_active_targets_runtime_failure_notifications(
                app=app,
                reason=SCHEDULER_RUNTIME_REASON,
                message="Target page, context or browser has been closed",
                worker_path="resident_scheduler",
                exception_class="RuntimeError",
            )
        )
        third_state = app.repositories.runtime_states.get(active.id)
        entries = app.repositories.notification_outbox.list_pending()
        active_run = app.repositories.scan_runs.latest_by_target(active.id)
        stopped_run = app.repositories.scan_runs.latest_by_target(stopped.id)
        paused_run = app.repositories.scan_runs.latest_by_target(paused.id)
        errored_run = app.repositories.scan_runs.latest_by_target(errored.id)
        errored_state = app.repositories.runtime_states.get(errored.id)

    assert first_count == 1
    assert first_state is not None
    assert first_state.runtime_status == TargetRuntimeStatus.IDLE
    assert first_state.scan_requested_at is not None
    assert first_state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert first_state.consecutive_failure_count == 1
    assert first_entries == []
    assert second_count == 1
    assert second_state is not None
    assert second_state.runtime_status == TargetRuntimeStatus.IDLE
    assert second_state.scan_requested_at is not None
    assert second_state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert second_state.consecutive_failure_count == 2
    assert second_entries == []
    assert third_count == 1
    assert third_state is not None
    assert third_state.runtime_status == TargetRuntimeStatus.ERROR
    assert third_state.consecutive_failure_reason == SCHEDULER_RUNTIME_REASON
    assert third_state.consecutive_failure_count == 3
    assert active_run is not None
    assert active_run.metadata["worker"] == "resident_scheduler"
    assert active_run.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert active_run.metadata["retry_streak"] == 3
    assert active_run.metadata["retry_limit"] == 3
    assert active_run.metadata["recovery_action"] == SCHEDULER_RUNTIME_RESTART_ACTION
    assert "auto_restart" not in active_run.metadata
    assert "已連續 3 次失敗" in active_run.error_message
    assert "會重啟" not in active_run.error_message
    assert stopped_run is None
    assert paused_run is None
    assert errored_run is None
    assert errored_state is not None
    assert errored_state.runtime_status == TargetRuntimeStatus.ERROR
    assert len(entries) == 1
    entry = entries[0]
    assert entry.target_id == active.id
    assert entry.event_kind == NotificationEventKind.RUNTIME_FAILURE
    assert entry.dedupe_id is not None
    assert entry.source_scan_run_id is not None
    assert entry.failure_reason == SCHEDULER_RUNTIME_REASON
    assert entry.failure_count == 3
    assert entry.item_key.startswith("runtime-failure:")
    assert "背景掃描執行錯誤" in entry.message
    assert "連續次數: 3" in entry.message
    assert "系統已停止此監視項目" in entry.message
    assert "系統已記錄背景掃描錯誤" not in entry.message
    assert dispatch_calls == [db_path]


def test_active_targets_unknown_runtime_failure_uses_default_retry_limit(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """未列入 terminal denylist 的全域錯誤預設第三次才通知。"""

    db_path = tmp_path / "app.db"
    dispatch_calls = _stub_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active-unknown",
                canonical_url="https://www.facebook.com/groups/active-unknown",
                group_name="Active target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.services.targets.restart_target_monitoring(active.id)

        for attempt in range(1, 4):
            count = (
                scan_failure_finalize_module.record_active_targets_runtime_failure_notifications(
                    app=app,
                    reason="unknown",
                    message="unexpected resident failure",
                    worker_path="resident_scheduler",
                    exception_class="RuntimeError",
                )
            )
            state = app.repositories.runtime_states.get(active.id)
            latest_scan = app.repositories.scan_runs.latest_by_target(active.id)
            entries = app.repositories.notification_outbox.list_pending()

            assert count == 1
            assert state is not None
            assert latest_scan is not None
            assert latest_scan.metadata["reason"] == "unknown"
            assert latest_scan.metadata["retry_streak"] == attempt
            assert latest_scan.metadata["retry_limit"] == 3
            if attempt < 3:
                assert state.runtime_status == TargetRuntimeStatus.IDLE
                assert state.scan_requested_at is not None
                assert entries == []
                assert latest_scan.metadata["runtime_action"] == "will_retry"
                assert latest_scan.metadata["retryable"] is True
                assert latest_scan.metadata["auto_restart"] is True
                assert latest_scan.metadata["recovery_action"] == TARGET_PAGE_RESTART_ACTION
            else:
                assert state.runtime_status == TargetRuntimeStatus.ERROR
                assert state.consecutive_failure_count == 3
                assert "已連續 3 次失敗" in latest_scan.error_message
                assert "會重啟" not in latest_scan.error_message
                assert latest_scan.metadata["runtime_action"] == "error"
                assert latest_scan.metadata["retryable"] is False
                assert "auto_restart" not in latest_scan.metadata
                assert latest_scan.metadata["recovery_action"] == TARGET_PAGE_RESTART_ACTION
                assert len(entries) == 1
                assert entries[0].failure_reason == "unknown"
                assert entries[0].failure_count == 3

    assert dispatch_calls == [db_path]


def test_active_targets_runtime_failure_immediate_notify_for_non_retryable_reason(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """非 retryable 全域錯誤仍要立即通知並停止 target。"""

    db_path = tmp_path / "app.db"
    dispatch_calls = _stub_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active-non-retryable",
                canonical_url="https://www.facebook.com/groups/active-non-retryable",
                group_name="Active target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.services.targets.restart_target_monitoring(active.id)
        count = scan_failure_finalize_module.record_active_targets_runtime_failure_notifications(
            app=app,
            reason="login_required",
            message="login required",
            worker_path="resident_scheduler",
            exception_class="RuntimeError",
        )
        active_run = app.repositories.scan_runs.latest_by_target(active.id)
        active_state = app.repositories.runtime_states.get(active.id)
        entries = app.repositories.notification_outbox.list_pending()

    assert count == 1
    assert active_run is not None
    assert active_run.metadata["worker"] == "resident_scheduler"
    assert active_run.metadata["reason"] == "login_required"
    assert active_state is not None
    assert active_state.runtime_status == TargetRuntimeStatus.ERROR
    assert len(entries) == 1
    entry = entries[0]
    assert entry.target_id == active.id
    assert entry.event_kind == NotificationEventKind.RUNTIME_FAILURE
    assert entry.source_scan_run_id is not None
    assert entry.failure_reason == "login_required"
    assert entry.failure_count == 1
    assert entry.item_key.startswith("runtime-failure:")
    assert "系統已停止此監視項目" in entry.message
    assert dispatch_calls == [db_path]


def test_immediate_terminal_failure_records_again_after_manual_restart(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """同一 terminal 錯誤在手動重啟後再次發生，仍要新增 scan run 並通知。"""

    db_path = tmp_path / "app.db"
    dispatch_calls = _stub_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="terminal-repeat",
                canonical_url="https://www.facebook.com/groups/terminal-repeat",
                group_name="Terminal target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.mark_target_running(
            target.id,
            "worker-a",
            page_id="page-a",
        )
        first_decision = record_guarded_scan_failure(
            app=app,
            target_id=target.id,
            reason="login_required",
            message="login required",
            source="worker_failure",
            worker_path="resident_main",
            commit_guard=scan_commit_guard_from_runtime_state(running),
        )
        first_run = app.repositories.scan_runs.latest_by_target(target.id)
        first_run_id = app.repositories.scan_runs.connection.execute(
            "SELECT MAX(id) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]

    assert first_decision is not None
    assert first_run is not None

    with SqliteApplicationContext(db_path) as app:
        app.services.targets.restart_target_monitoring(target.id)
        running = app.services.targets.mark_target_running(
            target.id,
            "worker-b",
            page_id="page-b",
        )
        second_decision = record_guarded_scan_failure(
            app=app,
            target_id=target.id,
            reason="login_required",
            message="login required",
            source="worker_failure",
            worker_path="resident_main",
            commit_guard=scan_commit_guard_from_runtime_state(running),
        )
        second_run = app.repositories.scan_runs.latest_by_target(target.id)
        second_run_id = app.repositories.scan_runs.connection.execute(
            "SELECT MAX(id) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        run_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (target.id,),
        ).fetchone()[0]
        entries = app.repositories.notification_outbox.list_pending()

    assert second_decision is not None
    assert second_run is not None
    assert first_run_id is not None
    assert second_run_id is not None
    assert second_run_id != first_run_id
    assert run_count == 2
    assert len(entries) == 2
    assert [entry.failure_reason for entry in entries] == [
        "login_required",
        "login_required",
    ]
    assert dispatch_calls == [db_path, db_path]


def test_runtime_failure_outbox_dispatch_preserves_event_kind(tmp_path: Path) -> None:
    """runtime_failure outbox 送出後，notification_events 也要保留 failure 語義。"""

    sent_messages: list[str] = []

    def fake_ntfy_sender(config: NtfyConfig, title: str, message: str) -> NtfyResult:
        sent_messages.append(message)
        return NtfyResult(ok=True, status_code=200, message="sent")

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="runtime-event",
                canonical_url="https://www.facebook.com/groups/runtime-event",
                group_name="Runtime target",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        config = app.services.targets.get_config_for_target(target)
        entries = enqueue_runtime_failure_notifications(
            app=app,
            target=target,
            config=config,
            scan_run_id=123,
            reason=SCHEDULER_RUNTIME_REASON,
            failure_count=3,
            error_message="背景掃描執行錯誤",
        )
        duplicate_entries = enqueue_runtime_failure_notifications(
            app=app,
            target=target,
            config=config,
            scan_run_id=123,
            reason=SCHEDULER_RUNTIME_REASON,
            failure_count=3,
            error_message="背景掃描執行錯誤",
        )

        sent_count = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        event = app.repositories.notification_events.latest_by_target(target.id)
        assert entries[0].dedupe_id is not None
        dedupe_row = app.repositories.notification_outbox.connection.execute(
            """
            SELECT
                event_kind,
                status,
                logical_item_id,
                failure_reason,
                failure_count,
                notification_event_id
            FROM notification_dedupe
            WHERE id = ?
            """,
            (entries[0].dedupe_id,),
        ).fetchone()

    assert len(entries) == 1
    assert duplicate_entries == ()
    assert sent_count == 1
    assert sent_messages
    assert event is not None
    assert event.event_kind == NotificationEventKind.RUNTIME_FAILURE
    assert event.source_scan_run_id == 123
    assert event.failure_reason == SCHEDULER_RUNTIME_REASON
    assert event.failure_count == 3
    assert dedupe_row is not None
    assert dedupe_row["event_kind"] == NotificationEventKind.RUNTIME_FAILURE.value
    assert dedupe_row["status"] == NotificationDedupeStatus.SENT.value
    assert dedupe_row["logical_item_id"] is None
    assert dedupe_row["failure_reason"] == SCHEDULER_RUNTIME_REASON
    assert dedupe_row["failure_count"] == 3
    assert dedupe_row["notification_event_id"] is not None


def test_runtime_failure_outbox_blocks_recoverable_unknown_before_retry_limit(
    tmp_path: Path,
) -> None:
    """通知入口本身也要擋住未達 retry limit 的 recoverable failure。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="runtime-unknown",
                canonical_url="https://www.facebook.com/groups/runtime-unknown",
                group_name="Runtime unknown",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        config = app.services.targets.get_config_for_target(target)

        first_entries = enqueue_runtime_failure_notifications(
            app=app,
            target=target,
            config=config,
            scan_run_id=124,
            reason=UNKNOWN_REASON,
            failure_count=1,
            error_message="未分類錯誤",
        )
        terminal_entries = enqueue_runtime_failure_notifications(
            app=app,
            target=target,
            config=config,
            scan_run_id=125,
            reason=UNKNOWN_REASON,
            failure_count=3,
            error_message="未分類錯誤",
        )

    assert first_entries == ()
    assert len(terminal_entries) == 1
    assert terminal_entries[0].failure_reason == UNKNOWN_REASON
    assert terminal_entries[0].failure_count == 3


def test_runtime_failure_after_commit_queue_blocks_preterminal_unknown(
    tmp_path: Path,
) -> None:
    """after-commit wrapper 不應為未達 retry limit 的錯誤註冊 dispatch。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="runtime-queue-unknown",
                canonical_url="https://www.facebook.com/groups/runtime-queue-unknown",
                group_name="Runtime queue unknown",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        config = app.services.targets.get_config_for_target(target)

        entries = queue_runtime_failure_notifications_after_commit(
            app=app,
            target=target,
            config=config,
            scan_run_id=127,
            reason=UNKNOWN_REASON,
            failure_count=1,
            error_message="未分類錯誤",
        )

        pending = app.repositories.notification_outbox.list_pending()
        after_commit_hooks = list(app.after_commit_hooks)

    assert entries == ()
    assert pending == []
    assert after_commit_hooks == []


def test_runtime_failure_outbox_allows_immediate_terminal_failure(
    tmp_path: Path,
) -> None:
    """立即 terminal 的登入類錯誤仍要第一次就通知。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="runtime-login",
                canonical_url="https://www.facebook.com/groups/runtime-login",
                group_name="Runtime login",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-test",
                ),
            )
        )
        config = app.services.targets.get_config_for_target(target)

        entries = enqueue_runtime_failure_notifications(
            app=app,
            target=target,
            config=config,
            scan_run_id=126,
            reason=f" {LOGIN_REQUIRED_REASON} ",
            failure_count=1,
            error_message="需要重新登入",
        )

    assert len(entries) == 1
    assert entries[0].failure_reason == LOGIN_REQUIRED_REASON
    assert entries[0].failure_count == 1


def test_record_skipped_scan_starts_write_transaction_before_scan_run_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sort-adjust skip 的 guard check 與 scan run 寫入也要同 transaction。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    saw_write_transaction: list[bool] = []
    with SqliteApplicationContext(db_path) as app:
        original_record_scan = app.services.scans.record_scan

        def record_scan_with_assertion(request: object) -> int:
            """記錄 skipped scan run 寫入前是否已持有 transaction。"""

            saw_write_transaction.append(
                app.repositories.runtime_states.connection.in_transaction
            )
            return original_record_scan(request)  # type: ignore[arg-type]

        monkeypatch.setattr(app.services.scans, "record_scan", record_scan_with_assertion)
        record_skipped_scan(
            app=app,
            target=fixture.target,
            metadata={"worker": "test_worker"},
            commit_guard=fixture.commit_guard,
        )

    assert saw_write_transaction == [True]


def test_record_skipped_scan_escalates_on_third_sort_skip(
    tmp_path: Path,
) -> None:
    """第三次排序保護性 skip 應升級成 WorkerFailure，不再寫 skipped success。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        record_skipped_scan(
            app=app,
            target=fixture.target,
            metadata={"worker": "test_worker"},
            commit_guard=fixture.commit_guard,
        )
        state = app.services.targets.mark_target_running(
            fixture.target.id,
            "worker-b",
            page_id="page-b",
        )
        second_guard = scan_commit_guard_from_runtime_state(state)
        record_skipped_scan(
            app=app,
            target=fixture.target,
            metadata={"worker": "test_worker"},
            commit_guard=second_guard,
        )
        state = app.services.targets.mark_target_running(
            fixture.target.id,
            "worker-c",
            page_id="page-c",
        )
        third_guard = scan_commit_guard_from_runtime_state(state)

        with pytest.raises(WorkerFailure) as excinfo:
            record_skipped_scan(
                app=app,
                target=fixture.target,
                metadata={"worker": "test_worker"},
                commit_guard=third_guard,
            )

        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        runtime_state = app.repositories.runtime_states.get(fixture.target.id)
        scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ?",
            (fixture.target.id,),
        ).fetchone()[0]

    assert excinfo.value.reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["skip_streak"] == 2
    assert scan_count == 2
    assert runtime_state is not None
    assert runtime_state.runtime_status == TargetRuntimeStatus.RUNNING
    assert runtime_state.consecutive_scan_skip_count == 2


def test_sort_adjust_skip_notifies_after_three_escalated_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 次排序 skip 折算 1 次 failure；第 3 次 failure 才排 runtime 通知。"""

    queued_notifications: list[dict[str, object]] = []

    def fake_queue_runtime_failure_notifications_after_commit(
        **kwargs: object,
    ) -> tuple[object, ...]:
        """記錄 terminal runtime failure notification 參數，不做外部 I/O。"""

        queued_notifications.append(dict(kwargs))
        return ()

    monkeypatch.setattr(
        scan_failure_finalize_module,
        "queue_runtime_failure_notifications_after_commit",
        fake_queue_runtime_failure_notifications_after_commit,
    )

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        target = _activate_target(app, target)
        decisions = []
        for _attempt_index in range(9):
            try:
                record_skipped_scan(
                    app=app,
                    target=target,
                    metadata={"worker": "test_worker"},
                    commit_guard=UNGUARDED_SCAN_COMMIT,
                )
            except WorkerFailure as exc:
                decision = record_guarded_scan_failure(
                    app=app,
                    target_id=target.id,
                    reason=exc.reason,
                    message=str(exc),
                    source="worker_failure",
                    worker_path="test_worker",
                    commit_guard=UNGUARDED_SCAN_COMMIT,
                    exception_class=exc.__class__.__name__,
                )
                assert decision is not None
                decisions.append(decision)

        state = app.repositories.runtime_states.get(target.id)
        success_count = app.repositories.scan_runs.connection.execute(
            """
            SELECT COUNT(*) FROM scan_runs
            WHERE target_id = ? AND status = ?
            """,
            (target.id, ScanStatus.SUCCESS.value),
        ).fetchone()[0]
        failed_count = app.repositories.scan_runs.connection.execute(
            """
            SELECT COUNT(*) FROM scan_runs
            WHERE target_id = ? AND status = ?
            """,
            (target.id, ScanStatus.FAILED.value),
        ).fetchone()[0]

    assert [decision.retry_streak for decision in decisions] == [1, 2, 3]
    assert decisions[0].auto_restart is True
    assert decisions[1].auto_restart is True
    assert decisions[2].terminal is True
    assert success_count == 6
    assert failed_count == 3
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert state.consecutive_failure_reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert state.consecutive_failure_count == 3
    assert state.consecutive_scan_skip_count == 0
    assert len(queued_notifications) == 1
    assert queued_notifications[0]["reason"] == SORT_ADJUST_UNCONFIRMED_REASON
    assert queued_notifications[0]["failure_count"] == 3


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
        assert second_result.latest_items[0].debug_metadata["classification"][
            "eligible_for_notify"
        ] is False
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
        retention_result = app.repositories.maintenance.prune_bounded_retention(
            now=utc_now()
        )
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

    assert sent_ntfy == ["phase0test"]

    with SqliteApplicationContext(db_path) as app:
        reloaded_target = app.repositories.targets.get(target_id)
        assert reloaded_target is not None
        clear_result = app.services.targets.reset_target_notification_state(
            reloaded_target.id
        )
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
        assert second_result.new_count == 1
        assert not second_result.baseline_mode
        assert second_result.latest_items[0].debug_metadata["classification"][
            "eligible_for_notify"
        ] is True
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
        assert baseline_result.latest_items[0].debug_metadata["classification"][
            "eligible_for_notify"
        ] is False
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

            cast(Any, app.repositories.latest_scan_items).replace_for_target = (
                fail_replace_for_target
            )
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
                notification_sender=fake_ntfy_sender,
            )
    except RuntimeError as exc:
        assert str(exc) == "latest_write_failed"

    assert sent_ntfy == []
    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is None


def test_outbox_keeps_retryable_failed_notification_after_commit(
    tmp_path: Path,
) -> None:
    """DB commit 成功但通知 dispatch 失敗時，outbox 保留 failed 狀態供重試。"""

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
            notification_sender=failing_ntfy_sender,
        )

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
) -> None:
    """同一輪多筆 match 只註冊一次 dispatch，不因 match 數倍增 attempts。"""

    calls: list[str] = []

    def failing_ntfy_sender(config: NtfyConfig, _title: str, _message: str) -> NtfyResult:
        """記錄 sender 被呼叫次數並模擬外部 I/O 失敗。"""

        calls.append(config.topic)
        raise RuntimeError("down")

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
            notification_sender=failing_ntfy_sender,
        )

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
            notification_sender=failed_result_sender,
        )

    with SqliteApplicationContext(db_path) as app:
        stored_target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, "123")
        assert stored_target is not None
        events = app.repositories.notification_events.list_by_target(stored_target.id)

    assert len(calls) == 2
    assert len(events) == 2
    assert all(event.status == NotificationStatus.FAILED for event in events)
    assert all(event.message == "failed_result" for event in events)


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
            notification_sender=sometimes_failing_sender,
        )

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
            notification_sender=sometimes_failing_sender,
        )

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
            notification_sender=sometimes_failing_sender,
        )

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

        sent_count = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )

    assert sent_count == 1
    assert in_transaction_during_send == [False]


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

        processed_count = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        entry = app.repositories.notification_outbox.get_by_idempotency_key(
            idempotency_key
        )
        event = app.repositories.notification_events.latest_by_target(target.id)

    assert processed_count == 1
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

        processed_count = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        entry = app.repositories.notification_outbox.get_by_idempotency_key(
            idempotency_key
        )
        event = app.repositories.notification_events.latest_by_target(target.id)

    assert processed_count == 1
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
        assert claimed[0].status == NotificationOutboxStatus.PROCESSING_FAILED
        app.repositories.notification_outbox.connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (claimed[0].id,),
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
            notification_sender=fake_ntfy_sender,
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

        sent_count = dispatch_new_pending_notification_outbox(
            app=app,
            ntfy_sender=fake_ntfy_sender,
        )
        pending = app.repositories.notification_outbox.list_pending(limit=20)

    assert sent_count == 11
    assert len(sent_topics) == 11
    assert len(pending) == 0


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
        )
        app.repositories.notification_outbox.mark_result(
            entry_id=entry.id or 0,
            status=NotificationOutboxStatus.FAILED,
            attempts=1,
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
