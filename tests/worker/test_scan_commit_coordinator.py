"""Scan commit coordinator tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_STOPPING_REASON
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.outbox_service import build_notification_idempotency_key
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.scan_commit_coordinator import FailureScanCommitRequest
from facebook_monitor.worker.scan_commit_coordinator import commit_failure_request_for_db_async
from facebook_monitor.worker.scan_commit_coordinator import (
    commit_guarded_idle_after_success,
)
from facebook_monitor.worker.scan_commit_coordinator import commit_guarded_protective_skip
from facebook_monitor.worker.scan_commit_coordinator import commit_success
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import NormalizedScanItem
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state
from facebook_monitor.worker.scan_pipeline_results import SuccessScanResult

from tests.worker.scan_finalize_test_helpers import _activate_target
from tests.worker.scan_finalize_test_helpers import _create_running_target_with_guard
from tests.worker.scan_finalize_test_helpers import _stub_outbox_dispatch


async def _commit_failure_request_for_test(
    *,
    db_path: Path,
    target_id: str,
    reason: str,
    message: str,
    source: ScanFailureSource,
    worker_path: str,
    commit_guard: ScanCommitGuard,
    exception_class: str = "",
) -> ScanCommitOutcome:
    """測試用薄 helper：讓案例都走 typed request commit path。"""

    return await commit_failure_request_for_db_async(
        FailureScanCommitRequest(
            db_path=db_path,
            target_id=target_id,
            reason=reason,
            message=message,
            source=source,
            worker_path=worker_path,
            commit_guard=commit_guard,
            exception_class=exception_class,
        )
    )


def test_scan_commit_coordinator_commits_idle_with_existing_guard(
    tmp_path: Path,
) -> None:
    """success finalize 後的 idle wrapper 只包既有 guarded idle helper。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        outcome = commit_guarded_idle_after_success(
            app=app,
            target_id=fixture.target.id,
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.IDLE_COMMITTED
    assert outcome.committed_visible_scan_state is False
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""


def test_scan_commit_coordinator_idle_reports_guard_mismatch_without_overwrite(
    tmp_path: Path,
) -> None:
    """舊 success attempt 的 idle wrapper 不可覆寫新的 running owner。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        current_state = app.services.targets.mark_target_running(
            fixture.target.id,
            "worker-b",
            page_id="page-b",
        )
        outcome = commit_guarded_idle_after_success(
            app=app,
            target_id=fixture.target.id,
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.stale_or_inactive is True
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-b"
    assert state.active_page_id == "page-b"
    assert state.last_started_at == current_state.last_started_at


def test_scan_commit_coordinator_commits_success_and_idle(
    tmp_path: Path,
) -> None:
    """success coordinator 擁有 finalize writes 與 guarded idle commit。"""

    db_path = tmp_path / "app.db"
    sent_messages: list[str] = []

    def fake_ntfy_sender(_config: NtfyConfig, _title: str, message: str) -> NtfyResult:
        """記錄 success coordinator after-commit 通知。"""

        sent_messages.append(message)
        return NtfyResult(ok=True, status_code=200, message="sent")

    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        config = replace(
            fixture.config,
            enable_ntfy=True,
            ntfy_topic="phase6-success",
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:coordinator-success",
                        alias_keys=("post:coordinator-success",),
                        group_id=fixture.target.group_id,
                        author="作者",
                        text="這是一篇票券貼文",
                        permalink=f"{fixture.target.canonical_url}/posts/1",
                    ),
                ),
                item_count=1,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
            notification_sender=fake_ntfy_sender,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        history = app.repositories.match_history.list_by_target(fixture.target.id)
        outbox_entry = app.repositories.notification_outbox.get_by_idempotency_key(
            build_notification_idempotency_key(
                target_id=fixture.target.id,
                item_key="post:coordinator-success",
                channel=NotificationChannel.NTFY,
            )
        )

    assert outcome.kind == ScanCommitOutcomeKind.SUCCESS_COMMITTED
    assert outcome.committed_visible_scan_state is True
    assert outcome.scan_run_id > 0
    assert outcome.new_count == 1
    assert outcome.matched_count == 1
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.active_worker_id == ""
    assert state.active_page_id == ""
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["worker"] == "phase6"
    assert len(latest_items) == 1
    assert latest_items[0].item_key == "post:coordinator-success"
    assert len(history) == 1
    assert outbox_entry is not None
    assert outbox_entry.source_scan_run_id is None


def test_scan_commit_coordinator_success_reports_guard_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """success coordinator 遇 stale guard 時不得寫 visible scan state。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app, include_keywords=("票券",))
        old_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(
            replace(old_state, active_worker_id="worker-b", active_page_id="page-b")
        )
        outcome = commit_success(
            app=app,
            target=fixture.target,
            config=fixture.config,
            result=SuccessScanResult(
                target_id=fixture.target.id,
                url=fixture.target.canonical_url,
                items=(
                    NormalizedScanItem(
                        item_kind=ItemKind.POST,
                        item_key="post:stale-success",
                        alias_keys=("post:stale-success",),
                        group_id=fixture.target.group_id,
                        text="票券",
                    ),
                ),
                item_count=1,
                metadata={"worker": "phase6"},
            ),
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        history = app.repositories.match_history.list_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()
        seen_count = app.repositories.seen_items.connection.execute(
            "SELECT COUNT(*) FROM seen_items WHERE scope_id = ?",
            (fixture.target.scope_id,),
        ).fetchone()[0]

    assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
    assert outcome.committed_visible_scan_state is False
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == "worker-b"
    assert state.active_page_id == "page-b"
    assert latest_scan is None
    assert latest_items == []
    assert history == []
    assert pending_outbox == []
    assert seen_count == 0


def test_scan_commit_coordinator_commits_guarded_failure(
    tmp_path: Path,
) -> None:
    """failure wrapper 回傳 existing failure decision 與 typed outcome。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.FAILURE_COMMITTED
        assert outcome.committed_visible_scan_state is True
        assert outcome.scan_run_id > 0
        assert outcome.runtime_failure_notification_count == 0
        assert outcome.failure_decision is not None
        assert outcome.reason == outcome.failure_decision.reason
        assert outcome.discard_page == outcome.failure_decision.discard_page

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_failure_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["reason"] == UNKNOWN_REASON


def test_scan_commit_coordinator_commits_failure_request(
    tmp_path: Path,
) -> None:
    """typed failure request path 保留 existing guarded failure finalize 語義。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> None:
        outcome = await commit_failure_request_for_db_async(
            FailureScanCommitRequest(
                db_path=db_path,
                target_id=fixture.target.id,
                reason=UNKNOWN_REASON,
                message="boom",
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=fixture.commit_guard,
                exception_class="RuntimeError",
                page_reused=False,
            )
        )
        assert outcome.kind == ScanCommitOutcomeKind.FAILURE_COMMITTED
        assert outcome.scan_run_id > 0
        assert outcome.failure_decision is not None
        assert outcome.reason == UNKNOWN_REASON

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)

    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.FAILED
    assert latest_scan.metadata["page_reused"] is False


def test_scan_commit_coordinator_failure_duplicate_reports_no_scan_run_write(
    tmp_path: Path,
) -> None:
    """duplicate non-terminal failure 不應被標成新增 visible failure scan。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> tuple[int, int, bool, int]:
        first = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=SCHEDULER_STOPPING_REASON,
            message="resident scheduler is stopping",
            source="scheduler_cancel",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="CancelledError",
        )
        with SqliteApplicationContext(db_path) as app:
            running_state = app.services.targets.mark_target_running(
                fixture.target.id,
                "worker-2",
                page_id="page-2",
            )
            second_guard = scan_commit_guard_from_runtime_state(running_state)
        second = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=SCHEDULER_STOPPING_REASON,
            message="resident scheduler is stopping",
            source="scheduler_cancel",
            worker_path="resident_main",
            commit_guard=second_guard,
            exception_class="CancelledError",
        )
        return (
            first.scan_run_id,
            second.scan_run_id,
            second.committed_visible_scan_state,
            second.runtime_failure_notification_count,
        )

    first_scan_run_id, second_scan_run_id, committed, outbox_count = asyncio.run(
        run_test()
    )

    with SqliteApplicationContext(db_path) as app:
        failed_scan_count = app.repositories.scan_runs.connection.execute(
            "SELECT COUNT(*) FROM scan_runs WHERE target_id = ? AND status = ?",
            (fixture.target.id, ScanStatus.FAILED.value),
        ).fetchone()[0]

    assert first_scan_run_id > 0
    assert second_scan_run_id == 0
    assert committed is False
    assert outbox_count == 0
    assert failed_scan_count == 1


def test_scan_commit_coordinator_failure_reports_runtime_outbox_count(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """terminal runtime failure outcome 要帶出實際 queued outbox count。"""

    db_path = tmp_path / "app.db"
    _stub_outbox_dispatch(monkeypatch)
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="terminal-runtime",
                canonical_url="https://www.facebook.com/groups/terminal-runtime",
                group_name="Terminal runtime",
                config=TargetConfigPatch(
                    enable_ntfy=True,
                    ntfy_topic="runtime-topic",
                ),
            )
        )
        target = _activate_target(app, target)

    async def run_test() -> tuple[int, int]:
        latest_scan_run_id = 0
        latest_outbox_count = 0
        for index in range(3):
            with SqliteApplicationContext(db_path) as app:
                running_state = app.services.targets.mark_target_running(
                    target.id,
                    f"worker-{index}",
                    page_id=f"page-{index}",
                )
                commit_guard = scan_commit_guard_from_runtime_state(running_state)
            outcome = await _commit_failure_request_for_test(
                db_path=db_path,
                target_id=target.id,
                reason=SCHEDULER_RUNTIME_REASON,
                message="Target page, context or browser has been closed",
                source="unknown_exception",
                worker_path="resident_main",
                commit_guard=commit_guard,
                exception_class="RuntimeError",
            )
            latest_scan_run_id = outcome.scan_run_id
            latest_outbox_count = outcome.runtime_failure_notification_count
        return latest_scan_run_id, latest_outbox_count

    terminal_scan_run_id, terminal_outbox_count = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        latest_scan = app.repositories.scan_runs.latest_by_target(target.id)
        latest_scan_row_id = app.repositories.scan_runs.connection.execute(
            "SELECT id FROM scan_runs WHERE target_id = ? ORDER BY id DESC LIMIT 1",
            (target.id,),
        ).fetchone()[0]
        state = app.repositories.runtime_states.get(target.id)
        entries = app.repositories.notification_outbox.list_pending()

    assert terminal_scan_run_id > 0
    assert terminal_outbox_count == 1
    assert latest_scan is not None
    assert latest_scan_row_id == terminal_scan_run_id
    assert latest_scan.metadata["reason"] == SCHEDULER_RUNTIME_REASON
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.ERROR
    assert len(entries) == 1
    entry = entries[0]
    assert entry.event_kind == NotificationEventKind.RUNTIME_FAILURE
    assert entry.source_scan_run_id == terminal_scan_run_id
    assert entry.failure_reason == SCHEDULER_RUNTIME_REASON
    assert entry.failure_count == 3


def test_scan_commit_coordinator_commits_existing_protective_skip(
    tmp_path: Path,
) -> None:
    """skip coordinator 只包 guarded protective finalize。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        outcome = commit_guarded_protective_skip(
            app=app,
            target_id=fixture.target.id,
            target=fixture.target,
            metadata={
                "worker": "resident_main",
                "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
            },
            commit_guard=fixture.commit_guard,
        )
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)

    assert outcome.kind == ScanCommitOutcomeKind.SKIP_COMMITTED
    assert outcome.committed_visible_scan_state is True
    assert outcome.scan_run_id > 0
    assert outcome.reason == SORT_ADJUST_UNCONFIRMED_REASON
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.consecutive_scan_skip_count == 1
    assert latest_scan is not None
    assert latest_scan.status == ScanStatus.SUCCESS
    assert latest_scan.metadata["scan_skipped"] is True
    assert latest_items == []


def test_scan_commit_coordinator_skip_stale_owner_writes_nothing(
    tmp_path: Path,
) -> None:
    """stale protective skip 不得寫 visible scan state 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        current_state = app.services.targets.mark_target_running(
            fixture.target.id,
            "worker-b",
            page_id="page-b",
        )
        current_guard = scan_commit_guard_from_runtime_state(current_state)
        try:
            commit_guarded_protective_skip(
                app=app,
                target_id=fixture.target.id,
                target=fixture.target,
                metadata={
                    "worker": "resident_main",
                    "skip_reason": SORT_ADJUST_UNCONFIRMED_REASON,
                },
                commit_guard=fixture.commit_guard,
            )
        except WorkerFailure as exc:
            stale_error = exc
        else:
            raise AssertionError("stale skip commit should fail before writes")
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        latest_items = app.repositories.latest_scan_items.list_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert stale_error.reason == TARGET_STOPPED_REASON
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == current_guard.worker_id
    assert state.active_page_id == current_guard.page_id
    assert latest_scan is None
    assert latest_items == []
    assert pending_outbox == []


def test_scan_commit_coordinator_failure_reports_guard_mismatch_without_writes(
    tmp_path: Path,
) -> None:
    """failure wrapper 遇舊 guard 時不得寫 failure scan 或 runtime outbox。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        old_state = app.services.targets.ensure_runtime_state(fixture.target.id)
        app.repositories.runtime_states.save(
            replace(old_state, active_worker_id="worker-b", active_page_id="page-b")
        )
        current_guard = scan_commit_guard_from_runtime_state(
            app.services.targets.ensure_runtime_state(fixture.target.id)
        )

    async def run_test() -> None:
        outcome = await _commit_failure_request_for_test(
            db_path=db_path,
            target_id=fixture.target.id,
            reason=UNKNOWN_REASON,
            message="stale boom",
            source="unknown_exception",
            worker_path="resident_main",
            commit_guard=fixture.commit_guard,
            exception_class="RuntimeError",
        )
        assert outcome.kind == ScanCommitOutcomeKind.GUARD_MISMATCH
        assert outcome.failure_decision is None
        assert outcome.committed_visible_scan_state is False

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.runtime_states.get(fixture.target.id)
        latest_scan = app.repositories.scan_runs.latest_by_target(fixture.target.id)
        pending_outbox = app.repositories.notification_outbox.list_pending()

    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.RUNNING
    assert state.active_worker_id == current_guard.worker_id
    assert state.active_page_id == current_guard.page_id
    assert latest_scan is None
    assert pending_outbox == []
