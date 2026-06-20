"""Shared scan finalize tests。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import NotificationDedupeStatus
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import TARGET_PAGE_RESTART_ACTION
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.notifications.outbox_runtime_failure_enqueue import (
    enqueue_runtime_failure_notifications,
)
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.outbox_dispatch_service import (
    dispatch_new_pending_notification_outbox,
)
from facebook_monitor.notifications.outbox_runtime_failure_enqueue import (
    queue_runtime_failure_notifications_after_commit,
)
from facebook_monitor.worker import scan_failure_finalize as scan_failure_finalize_module
from facebook_monitor.worker.scan_commit_guard import UNGUARDED_SCAN_COMMIT
from facebook_monitor.worker.scan_commit_guard import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_failure_finalize import (
    record_guarded_scan_failure_decision,
)
from facebook_monitor.worker.errors import WorkerFailure

from tests.worker.scan_finalize_test_helpers import record_protective_skip_for_test
from tests.worker.scan_finalize_test_helpers import _activate_target
from tests.worker.scan_finalize_test_helpers import _stub_outbox_dispatch
from tests.worker.scan_finalize_test_helpers import _create_running_target_with_guard


def test_record_guarded_scan_failure_ignores_stale_running_owner(
    tmp_path: Path,
) -> None:
    """failure finalize 遇到舊 owner guard 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        app.services.targets.restart_target_monitoring(fixture.target.id)
        refreshed_running = app.services.targets.mark_target_running(
            fixture.target.id,
            "worker-b",
            page_id="page-b",
        )

        decision = record_guarded_scan_failure_decision(
            app=app,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="stale failure should not commit",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )

        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        state = app.repositories.runtime_states.get(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert decision is None
    assert latest_scan is None
    assert pending_outbox == []
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-b"
    assert state.active_page_id == "page-b"
    assert state.last_started_at == refreshed_running.last_started_at


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
        active = app.services.targets.restart_target_monitoring(active.id)
        app.repositories.targets.save(
            replace(
                active,
                name="(20+) Active custom | Facebook",
                group_name="(20+) Active target | Facebook",
            )
        )
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
    assert "監視項目: Active custom" in entry.message
    assert "(20+)" not in entry.message
    assert "Active target" not in entry.message
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
        first_decision = record_guarded_scan_failure_decision(
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
        second_decision = record_guarded_scan_failure_decision(
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

        result = dispatch_new_pending_notification_outbox(
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
    assert result.dispatched_count == 1
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


def test_guarded_protective_skip_starts_write_transaction_before_scan_run_write(
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

            saw_write_transaction.append(app.repositories.runtime_states.connection.in_transaction)
            return original_record_scan(request)  # type: ignore[arg-type]

        monkeypatch.setattr(app.services.scans, "record_scan", record_scan_with_assertion)
        record_protective_skip_for_test(
            app=app,
            target=fixture.target,
            metadata={"worker": "test_worker"},
            commit_guard=fixture.commit_guard,
        )

    assert saw_write_transaction == [True]


def test_guarded_protective_skip_escalates_on_third_sort_skip(
    tmp_path: Path,
) -> None:
    """第三次排序保護性 skip 應升級成 WorkerFailure，不再寫 skipped success。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        record_protective_skip_for_test(
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
        record_protective_skip_for_test(
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
            record_protective_skip_for_test(
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
                record_protective_skip_for_test(
                    app=app,
                    target=target,
                    metadata={"worker": "test_worker"},
                    commit_guard=UNGUARDED_SCAN_COMMIT,
                )
            except WorkerFailure as exc:
                decision = record_guarded_scan_failure_decision(
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
