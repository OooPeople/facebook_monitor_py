"""Resident attempt outcome and cleanup unit tests."""

from __future__ import annotations

import asyncio

import pytest

from facebook_monitor.core.models import utc_now
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.attempt_cleanup import ResidentAttemptCleanupPlan
from facebook_monitor.worker.attempt_cleanup import run_resident_attempt_cleanup
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcome
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcomeKind
from facebook_monitor.worker.attempt_transitions import transition_from_attempt_outcome
from facebook_monitor.worker.attempt_transitions import transition_from_scan_commit_outcome
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_page_pool import PageOwnership
from facebook_monitor.worker.resident_main_queue import QueueItem
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcome
from facebook_monitor.worker.scan_commit_outcomes import ScanCommitOutcomeKind

from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import FakeAsyncPage
from tests.worker.resident_main_test_helpers import RecordingSchedulePlanner


def test_resident_attempt_outcome_adapter_preserves_public_result_shape() -> None:
    """typed outcome 目前只能映射回既有 AsyncTargetScanResult 語義。"""

    success = ResidentAttemptOutcome.succeeded(
        target_id="target-1",
        opened_page=True,
        reused_page=False,
    ).to_scan_result()
    failure = ResidentAttemptOutcome.failed(
        target_id="target-1",
        reason="boom",
        opened_page=False,
        reused_page=True,
    ).to_scan_result()
    skipped = ResidentAttemptOutcome.skipped(
        target_id="target-1",
        kind=ResidentAttemptOutcomeKind.SQLITE_LOCK_RETRY,
        reason="database_locked",
        opened_page=True,
    ).to_scan_result()
    runtime_restart = ResidentAttemptOutcome(
        kind=ResidentAttemptOutcomeKind.RUNTIME_RESTART_REQUESTED,
        target_id="target-1",
    ).to_scan_result()
    runtime_restart_constructed = ResidentAttemptOutcome.runtime_restart_requested(
        target_id="target-1",
        reason="scheduler_runtime",
    ).to_scan_result()

    assert (success.success, success.failure, success.skipped) == (True, False, False)
    assert (success.opened_page, success.reused_page) == (True, False)
    assert (failure.success, failure.failure, failure.skipped) == (False, True, False)
    assert (failure.opened_page, failure.reused_page) == (False, True)
    assert (skipped.success, skipped.failure, skipped.skipped) == (False, False, True)
    assert (skipped.opened_page, skipped.reused_page) == (True, False)
    assert (runtime_restart.success, runtime_restart.failure, runtime_restart.skipped) == (
        False,
        True,
        False,
    )
    assert runtime_restart_constructed.failure is True

    with pytest.raises(ValueError):
        ResidentAttemptOutcome.skipped(
            target_id="target-1",
            kind=ResidentAttemptOutcomeKind.FAILED,
        )


def test_scan_commit_outcome_classifies_visible_and_stale_results() -> None:
    """scan commit outcome 只描述結果分類，不執行任何 side effect。"""

    success = ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.SUCCESS_COMMITTED,
        target_id="target-1",
        scan_run_id=123,
        matched_count=1,
        new_count=1,
    )
    stale = ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
        target_id="target-1",
        reason="owner_changed",
    )
    sqlite_retry = ScanCommitOutcome(
        kind=ScanCommitOutcomeKind.SQLITE_LOCK_RETRY,
        target_id="target-1",
        reason="database_locked",
    )

    assert success.committed_visible_scan_state is True
    assert success.stale_or_inactive is False
    assert stale.committed_visible_scan_state is False
    assert stale.stale_or_inactive is True
    assert sqlite_retry.committed_visible_scan_state is False
    assert sqlite_retry.stale_or_inactive is False


def test_attempt_transition_maps_scan_commit_outcome_without_side_effects() -> None:
    """terminal transition 只產生 outcome 與 cleanup plan，不執行 cleanup。"""

    success = transition_from_scan_commit_outcome(
        target_id="target-1",
        owner_key="owner-1",
        page_id="page-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.IDLE_COMMITTED,
            target_id="target-1",
        ),
        opened_page=True,
        reused_page=False,
    )
    stale = transition_from_scan_commit_outcome(
        target_id="target-1",
        owner_key="owner-1",
        page_id="page-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
            target_id="target-1",
            reason="scan_commit_guard_mismatch",
        ),
        opened_page=True,
        reused_page=False,
    )
    committed_skip = transition_from_scan_commit_outcome(
        target_id="target-1",
        owner_key="owner-1",
        page_id="page-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.SKIP_COMMITTED,
            target_id="target-1",
            reason="sort_adjust_unconfirmed",
        ),
        opened_page=True,
        reused_page=False,
    )

    success_result = success.outcome.to_scan_result()
    stale_result = stale.outcome.to_scan_result()
    committed_skip_result = committed_skip.outcome.to_scan_result()

    assert success_result.success is True
    assert success_result.opened_page is True
    assert success.cleanup_plan == ResidentAttemptCleanupPlan.for_attempt(
        target_id="target-1",
        owner_key="owner-1",
        page_id="page-1",
    )
    assert stale.outcome.kind == ResidentAttemptOutcomeKind.OWNER_CHANGED
    assert stale_result.skipped is True
    assert stale_result.opened_page is False
    assert stale_result.reused_page is False
    assert committed_skip.outcome.kind == ResidentAttemptOutcomeKind.SKIPPED
    assert committed_skip.outcome.reason == "sort_adjust_unconfirmed"
    assert committed_skip_result.skipped is True
    assert committed_skip_result.opened_page is False
    assert committed_skip_result.reused_page is False


def test_attempt_transition_rejects_future_success_committed_placeholder() -> None:
    """SUCCESS_COMMITTED 需新設計，不可默默映射成 skipped。"""

    with pytest.raises(NotImplementedError):
        transition_from_scan_commit_outcome(
            target_id="target-1",
            owner_key="owner-1",
            page_id="page-1",
            commit_outcome=ScanCommitOutcome(
                kind=ScanCommitOutcomeKind.SUCCESS_COMMITTED,
                target_id="target-1",
            ),
            opened_page=True,
            reused_page=False,
        )


def test_attempt_transition_wraps_existing_terminal_outcome() -> None:
    """非 scan-commit branch 也只包 outcome 與 cleanup plan，不做 side effect。"""

    transition = transition_from_attempt_outcome(
        target_id="target-1",
        owner_key="owner-1",
        page_id="page-1",
        outcome=ResidentAttemptOutcome.skipped(
            target_id="target-1",
            kind=ResidentAttemptOutcomeKind.SQLITE_LOCK_RETRY,
            reason="database_locked",
            opened_page=True,
            reused_page=False,
        ),
    )

    result = transition.outcome.to_scan_result()

    assert result.skipped is True
    assert result.opened_page is True
    assert transition.cleanup_plan == ResidentAttemptCleanupPlan.for_attempt(
        target_id="target-1",
        owner_key="owner-1",
        page_id="page-1",
    )


def test_resident_attempt_cleanup_uses_owner_and_page_guards() -> None:
    """cleanup runner 必須沿用 queue owner 與 page id guard，不可清掉新 attempt。"""

    async def run_test() -> None:
        target_queue = TargetQueue()
        target_id = "target-1"
        assert await target_queue.enqueue(
            QueueItem(
                due_target=DueTarget(
                    target_id=target_id,
                    interval_seconds=60,
                    due_at=utc_now(),
                ),
                enqueue_reason="due",
                enqueued_at=utc_now(),
            )
        )
        assert await target_queue.get() is not None
        await target_queue.bind_running_owner(target_id, "new-owner")

        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        page = FakeAsyncPage()
        page_pool.pages[target_id] = PageOwnership(
            page=page,
            page_id="new-page",
            target_id=target_id,
            in_use_by_worker="worker-new",
            current_url="https://current.example",
        )
        planner = RecordingSchedulePlanner()
        unregistered: list[tuple[str, str]] = []

        class CleanupHost:
            """測試用 cleanup host。"""

            def __init__(self) -> None:
                self.page_pool: AsyncResidentPagePool = page_pool
                self.target_queue: TargetQueue = target_queue
                self.schedule_planner: TargetSchedulePlanner = planner

            async def _unregister_active_attempt(
                self,
                target_id: str,
                owner_key: str,
            ) -> None:
                unregistered.append((target_id, owner_key))

        await run_resident_attempt_cleanup(
            CleanupHost(),
            ResidentAttemptCleanupPlan.for_attempt(
                target_id=target_id,
                owner_key="old-owner",
                page_id="old-page",
            ),
        )

        assert await target_queue.snapshot() == (0, 1, ())
        assert page_pool.pages[target_id].in_use_by_worker == "worker-new"
        assert page_pool.pages[target_id].current_url == "https://current.example"
        assert not page.closed
        assert planner.finished_target_ids == [target_id]
        assert unregistered == [(target_id, "old-owner")]

    asyncio.run(run_test())
