"""Resident attempt outcome and cleanup unit tests."""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import pytest
from playwright.async_api import Error as AsyncPlaywrightError

from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.core.scan_failure_policy import SCHEDULER_RUNTIME_RESTART_ACTION
from facebook_monitor.core.scan_failure_policy import ScanFailureDecision
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.worker.attempt_cleanup import ResidentAttemptCleanupPlan
from facebook_monitor.worker.attempt_cleanup import ResidentAttemptResources
from facebook_monitor.worker.attempt_cleanup import run_resident_attempt_cleanup
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcome
from facebook_monitor.worker.attempt_outcomes import ResidentAttemptOutcomeKind
from facebook_monitor.worker.attempt_transitions import transition_from_attempt_outcome
from facebook_monitor.worker.attempt_transitions import transition_from_scan_commit_outcome
from facebook_monitor.worker import resident_main_executor_attempt as attempt_module
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.resident_failure_decisions import decide_resident_failure_attempt
from facebook_monitor.worker.resident_failure_decisions import ResidentFailureAttemptDecision
from facebook_monitor.worker.resident_failure_decisions import ResidentFailureRecordDecision
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_playwright_exception,
)
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_runtime_restart_cancellation,
)
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_unknown_exception,
)
from facebook_monitor.worker.resident_failure_decisions import (
    failure_record_decision_for_worker_failure,
)
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
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.IDLE_COMMITTED,
            target_id="target-1",
        ),
        opened_page=True,
        reused_page=False,
    )
    stale = transition_from_scan_commit_outcome(
        target_id="target-1",
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
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.SKIP_COMMITTED,
            target_id="target-1",
            reason="sort_adjust_unconfirmed",
        ),
        opened_page=True,
        reused_page=False,
    )
    committed_success = transition_from_scan_commit_outcome(
        target_id="target-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.SUCCESS_COMMITTED,
            target_id="target-1",
            scan_run_id=123,
        ),
        opened_page=True,
        reused_page=False,
    )

    success_result = success.outcome.to_scan_result()
    stale_result = stale.outcome.to_scan_result()
    committed_skip_result = committed_skip.outcome.to_scan_result()
    committed_success_result = committed_success.outcome.to_scan_result()

    assert success_result.success is True
    assert success_result.opened_page is True
    assert committed_success_result.success is True
    assert committed_success_result.opened_page is True
    assert success.cleanup_plan is None
    assert committed_success.cleanup_plan is None
    assert stale.outcome.kind == ResidentAttemptOutcomeKind.OWNER_CHANGED
    assert stale_result.skipped is True
    assert stale_result.opened_page is False
    assert stale_result.reused_page is False
    assert committed_skip.outcome.kind == ResidentAttemptOutcomeKind.SKIPPED
    assert committed_skip.outcome.reason == "sort_adjust_unconfirmed"
    assert committed_skip_result.skipped is True
    assert committed_skip_result.opened_page is False
    assert committed_skip_result.reused_page is False


def test_attempt_transition_wraps_existing_terminal_outcome() -> None:
    """非 scan-commit branch 也只包 outcome 與 cleanup plan，不做 side effect。"""

    transition = transition_from_attempt_outcome(
        target_id="target-1",
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
    assert transition.cleanup_plan is None


def test_cleanup_plan_from_pre_admission_resources_only_completes_queue() -> None:
    """claim running 前沒有取得 page/planner/active token 時不做多餘 cleanup。"""

    plan = ResidentAttemptCleanupPlan.from_resources(
        target_id="target-1",
        resources=ResidentAttemptResources(queue_item_consumed=True),
    )

    assert plan.target_id == "target-1"
    assert plan.owner_key == ""
    assert plan.page_id == ""
    assert plan.complete_queue_item is True
    assert plan.unregister_active_attempt is False
    assert plan.release_page is False
    assert plan.mark_planner_finished is False


def test_cleanup_plan_does_not_release_reserved_but_unacquired_page() -> None:
    """只有 reserve page id 不代表 page 已交給 attempt 使用。"""

    plan = ResidentAttemptCleanupPlan.from_resources(
        target_id="target-1",
        resources=ResidentAttemptResources(
            queue_item_consumed=True,
            queue_owner_key="owner-1",
            page_id="page-1",
            page_acquired=False,
        ),
    )

    assert plan.owner_key == "owner-1"
    assert plan.page_id == ""
    assert plan.complete_queue_item is True
    assert plan.unregister_active_attempt is False
    assert plan.release_page is False
    assert plan.mark_planner_finished is False


def test_cleanup_plan_from_full_resources_runs_guarded_cleanup() -> None:
    """已取得的 resource tokens 會轉成對應 cleanup obligation。"""

    plan = ResidentAttemptCleanupPlan.from_resources(
        target_id="target-1",
        resources=ResidentAttemptResources(
            queue_item_consumed=True,
            queue_owner_key="queue-owner",
            active_attempt_key="active-owner",
            page_id="page-1",
            page_acquired=True,
            planner_dispatch_id="target-1",
        ),
    )

    assert plan.owner_key == "queue-owner"
    assert plan.active_attempt_key == "active-owner"
    assert plan.page_id == "page-1"
    assert plan.complete_queue_item is True
    assert plan.unregister_active_attempt is True
    assert plan.release_page is True
    assert plan.mark_planner_finished is True


def test_cleanup_runner_uses_separate_queue_and_active_attempt_guards() -> None:
    """queue complete 與 active unregister 必須使用各自的 owner token。"""

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
        await target_queue.bind_running_owner(target_id, "queue-owner")

        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
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
            ResidentAttemptCleanupPlan.from_resources(
                target_id=target_id,
                resources=ResidentAttemptResources(
                    queue_item_consumed=True,
                    queue_owner_key="queue-owner",
                    active_attempt_key="active-owner",
                ),
            ),
        )

        assert await target_queue.snapshot() == (0, 0, ())
        assert unregistered == [(target_id, "active-owner")]

    asyncio.run(run_test())


def test_failure_attempt_decision_maps_owner_changed_without_side_effects() -> None:
    """failure commit stale owner 只映射 skipped，不要求 discard/restart。"""

    decision = decide_resident_failure_attempt(
        target_id="target-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.GUARD_MISMATCH,
            target_id="target-1",
            reason="scan_failure_guard_mismatch",
        ),
        owner_changed_reason="worker_failure_owner_changed",
        source="worker_failure",
        exception_class="WorkerFailure",
        request_runtime_restart=True,
        opened_page=True,
        reused_page=False,
        include_page_counts_in_result=True,
    )

    result = decision.outcome.to_scan_result()

    assert decision.owner_changed is True
    assert decision.failure_decision is None
    assert decision.discard_page is False
    assert decision.request_runtime_restart is False
    assert decision.outcome.kind == ResidentAttemptOutcomeKind.OWNER_CHANGED
    assert decision.outcome.reason == "worker_failure_owner_changed"
    assert result.skipped is True
    assert result.opened_page is False


def test_failure_attempt_decision_maps_target_inactive_without_side_effects() -> None:
    """failure commit target inactive 只映射 skipped，不誤標為 owner changed。"""

    decision = decide_resident_failure_attempt(
        target_id="target-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.TARGET_INACTIVE,
            target_id="target-1",
            reason="target_inactive_before_commit",
        ),
        owner_changed_reason="worker_failure_owner_changed",
        source="worker_failure",
        exception_class="WorkerFailure",
        request_runtime_restart=True,
        opened_page=True,
        reused_page=False,
        include_page_counts_in_result=True,
    )

    result = decision.outcome.to_scan_result()

    assert decision.owner_changed is True
    assert decision.failure_decision is None
    assert decision.discard_page is False
    assert decision.request_runtime_restart is False
    assert decision.outcome.kind == ResidentAttemptOutcomeKind.TARGET_INACTIVE
    assert decision.outcome.reason == "target_inactive_before_commit"
    assert result.skipped is True
    assert result.opened_page is False


def test_failure_attempt_target_inactive_log_uses_outcome_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """target inactive cleanup log 應使用實際 terminal outcome reason。"""

    caplog.set_level(
        logging.INFO,
        logger="facebook_monitor.worker.resident_main_executor_attempt",
    )
    record_decision = ResidentFailureRecordDecision(
        reason="unknown",
        message="inactive boom",
        source="unknown_exception",
        exception_class="RuntimeError",
        owner_changed_reason="unknown_owner_changed",
    )
    attempt_decision = ResidentFailureAttemptDecision(
        outcome=ResidentAttemptOutcome.skipped(
            target_id="target-1",
            kind=ResidentAttemptOutcomeKind.TARGET_INACTIVE,
            reason="target_inactive_before_commit",
        ),
        failure_decision=None,
        owner_changed=True,
    )

    transition = asyncio.run(
        attempt_module._finish_failure_attempt_decision(  # noqa: SLF001
            pool=cast(attempt_module.ResidentExecutorAttemptHost, object()),
            worker_id="worker-a",
            state=attempt_module.ResidentQueueAttemptState(
                target_id="target-1",
                page_id="page-a",
            ),
            failure_record_decision=record_decision,
            failure_attempt_decision=attempt_decision,
        )
    )

    assert transition.outcome.reason == "target_inactive_before_commit"
    assert "reason=target_inactive_before_commit" in caplog.text
    assert "reason=unknown_owner_changed" not in caplog.text


def test_failure_attempt_decision_maps_regular_failure_with_page_counts() -> None:
    """ordinary failure 保留 discard_page 與 public failure counter 語義。"""

    scan_decision = ScanFailureDecision(
        reason="unknown",
        retryable=True,
        target_action="idle",
        runtime_action="will_retry",
        discard_page=True,
        counts_toward_streak=True,
        retry_streak=1,
        retry_limit=3,
    )
    decision = decide_resident_failure_attempt(
        target_id="target-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.FAILURE_COMMITTED,
            target_id="target-1",
            reason=scan_decision.reason,
            failure_decision=scan_decision,
        ),
        owner_changed_reason="unknown_owner_changed",
        source="unknown_exception",
        exception_class="RuntimeError",
        request_runtime_restart=True,
        opened_page=True,
        reused_page=False,
        include_page_counts_in_result=True,
    )

    result = decision.outcome.to_scan_result()

    assert decision.owner_changed is False
    assert decision.failure_decision == scan_decision
    assert decision.discard_page is True
    assert decision.request_runtime_restart is False
    assert decision.outcome.kind == ResidentAttemptOutcomeKind.FAILED
    assert decision.outcome.exception_class == "RuntimeError"
    assert result.failure is True
    assert result.opened_page is True
    assert result.reused_page is False


def test_failure_attempt_decision_maps_runtime_restart_request() -> None:
    """runtime restart recovery 明確映射 request_runtime_restart side effect。"""

    scan_decision = ScanFailureDecision(
        reason="scheduler_runtime",
        retryable=True,
        target_action="idle",
        runtime_action="will_retry",
        discard_page=True,
        counts_toward_streak=True,
        retry_streak=1,
        retry_limit=3,
        auto_restart=True,
        recovery_action=SCHEDULER_RUNTIME_RESTART_ACTION,
    )
    decision = decide_resident_failure_attempt(
        target_id="target-1",
        commit_outcome=ScanCommitOutcome(
            kind=ScanCommitOutcomeKind.FAILURE_COMMITTED,
            target_id="target-1",
            reason=scan_decision.reason,
            failure_decision=scan_decision,
        ),
        owner_changed_reason="runtime_restart_owner_changed",
        source="unknown_exception",
        exception_class="CancelledError",
        request_runtime_restart=False,
        opened_page=True,
        reused_page=False,
        include_page_counts_in_result=False,
    )

    result = decision.outcome.to_scan_result()

    assert decision.discard_page is True
    assert decision.request_runtime_restart is False
    assert decision.outcome.kind == ResidentAttemptOutcomeKind.RUNTIME_RESTART_REQUESTED
    assert decision.outcome.request_runtime_restart is False
    assert result.failure is True
    assert result.opened_page is False
    assert result.reused_page is False


def test_failure_record_decision_classifies_exception_branches() -> None:
    """exception branch 的 failure request 參數應可獨立測試。"""

    worker = failure_record_decision_for_worker_failure(
        WorkerFailure("worker_reason", "worker failed")
    )
    runtime_restart = failure_record_decision_for_runtime_restart_cancellation()
    playwright = failure_record_decision_for_playwright_exception(
        AsyncPlaywrightError("Timeout 30000ms exceeded")
    )
    unknown = failure_record_decision_for_unknown_exception(RuntimeError("boom"))

    assert worker.reason == "worker_reason"
    assert worker.source == "worker_failure"
    assert worker.owner_changed_reason == "worker_failure_owner_changed"
    assert worker.include_page_counts_in_result is True
    assert runtime_restart.reason == SCHEDULER_RUNTIME_REASON
    assert runtime_restart.request_runtime_restart is False
    assert runtime_restart.include_page_counts_in_log is False
    assert playwright.reason == PAGE_LOAD_TIMEOUT_REASON
    assert playwright.source == "playwright"
    assert unknown.reason == UNKNOWN_REASON
    assert unknown.source == "unknown_exception"


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
