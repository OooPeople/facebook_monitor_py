"""Scan commit coordinator tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.scan_failures import SORT_ADJUST_UNCONFIRMED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.worker.scan_commit_coordinator import commit_failure_for_db_async
from facebook_monitor.worker.scan_commit_coordinator import (
    commit_idle_after_existing_success_finalize,
)
from facebook_monitor.worker.scan_commit_coordinator import commit_skipped_existing_finalize
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind
from facebook_monitor.worker.scan_finalize import scan_commit_guard_from_runtime_state

from tests.worker.scan_finalize_test_helpers import _create_running_target_with_guard


def test_scan_commit_coordinator_commits_idle_with_existing_guard(
    tmp_path: Path,
) -> None:
    """success finalize 後的 idle wrapper 只包既有 guarded idle helper。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        outcome = commit_idle_after_existing_success_finalize(
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
        outcome = commit_idle_after_existing_success_finalize(
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


def test_scan_commit_coordinator_commits_guarded_failure(
    tmp_path: Path,
) -> None:
    """failure wrapper 回傳 existing failure decision 與 typed outcome。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)

    async def run_test() -> None:
        outcome = await commit_failure_for_db_async(
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


def test_scan_commit_coordinator_commits_existing_protective_skip(
    tmp_path: Path,
) -> None:
    """skip wrapper 只包既有 record_skipped_scan protective finalize。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        fixture = _create_running_target_with_guard(app)
        outcome = commit_skipped_existing_finalize(
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
        outcome = await commit_failure_for_db_async(
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
