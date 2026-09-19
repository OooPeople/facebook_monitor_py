"""Browser-free supervisor消費persistent manual half-open probe request。

職責：先以DB CAS claim唯一owner，之後才取得process coordinator、profile lease與
Playwright；每次probe最多一個page與一次group document navigation，不執行任何
target/product write。Comments recipe在group-first trusted-click完成前固定不claim。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.browser_runtime import launch_persistent_context_async
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookHalfOpenClaimOutcome
from facebook_monitor.core.facebook_access import FacebookProbeFinishOutcome
from facebook_monitor.core.facebook_access import FacebookProbeFinishResult
from facebook_monitor.core.facebook_access import FacebookProbeFailureStage
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_automation_pacing import FacebookAutomationPacingToken
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_SESSION_FAILURE_REASONS
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_automation_coordinator import FacebookAutomationLease
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWorkKind,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWaitCancelled,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardError,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardRuntime,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    APPROVED_FACEBOOK_RECOVERY_PROBE_RECIPES,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    build_facebook_probe_deadline,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    cancel_probe_monitor_task,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    execute_group_document_probe,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import FacebookProbeDeadline
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookProbeBrowserCancelled,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookProbeBrowserExecution,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookRecoveryProbeRecipe,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_orchestration import ensure_async_page_scannable


logger = logging.getLogger(__name__)


class FacebookManualProbeOutcome(StrEnum):
    """Supervisor消費persistent request的stable結果。"""

    NO_REQUEST = "no_request"
    RECIPE_UNAVAILABLE = "recipe_unavailable"
    CLAIM_REJECTED = "claim_rejected"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"
    STALE_OWNER = "stale_owner"


@dataclass(frozen=True)
class FacebookManualProbeExecutionResult:
    """回傳probe是否claim、是否launch與owner-aware finish狀態。"""

    outcome: FacebookManualProbeOutcome
    request_id: str = ""
    claimed: bool = False
    browser_launched: bool = False
    page_count: int = 0
    document_navigation_count: int = 0
    context_closed: bool = False
    cleanup_completed: bool = True
    finish_outcome: FacebookProbeFinishOutcome | None = None
    failure_reason: str = ""
    failure_stage: FacebookProbeFailureStage | None = None


@dataclass(frozen=True)
class _ClaimedManualProbe:
    """保存已由 DB CAS claim 的 immutable probe request。"""

    request_id: str
    circuit: FacebookAccessCircuitSnapshot
    target_id: str | None
    initial_target: TargetDescriptor | None
    recipe: FacebookRecoveryProbeRecipe


@dataclass
class _ManualProbeResourceState:
    """集中追蹤 claim 後資源 owner 與最後 observation。"""

    observed_result: FacebookProbeResult = FacebookProbeResult.INCONCLUSIVE
    browser_launched: bool = False
    page_count: int = 0
    document_count: int = 0
    context_closed: bool = False
    cleanup_completed: bool = True
    marker_trip_pending: bool = False
    failure_reason: str = ""
    failure_stage: FacebookProbeFailureStage | None = None
    propagate_cancel: bool = False
    process_lease: FacebookAutomationLease | None = None
    pacing_token: FacebookAutomationPacingToken | None = None

    def cancel(self, *, propagate: bool = False) -> None:
        """將執行收斂為 cancelled，必要時在 durable finish 後向上傳遞。"""

        self.observed_result = FacebookProbeResult.CANCELLED
        self.propagate_cancel = self.propagate_cancel or propagate

    def apply_browser_observation(
        self,
        observation: FacebookProbeBrowserExecution,
    ) -> None:
        """接收 browser helper 的完整 observation。"""

        self.observed_result = observation.result
        self.browser_launched = observation.browser_launched
        self.page_count = observation.page_count
        self.document_count = observation.document_count
        self.context_closed = observation.context_closed
        self.cleanup_completed = observation.cleanup_completed
        self.marker_trip_pending = observation.marker_trip_pending
        self.failure_reason = observation.failure_reason
        self.failure_stage = observation.failure_stage
        self.propagate_cancel = observation.cancelled


_APPROVED_RECIPES = APPROVED_FACEBOOK_RECOVERY_PROBE_RECIPES


async def consume_pending_facebook_manual_probe(
    *,
    options: ResidentRuntimeOptions,
    profile_scope_key: str,
    coordinator: FacebookAutomationCoordinator,
    admission_controller: FacebookAutomationAdmissionController,
    session_guard_runtime: FacebookAutomationSessionGuardRuntime,
    should_stop: Callable[[], bool],
    clock: Callable[[], datetime] = utc_now,
) -> FacebookManualProbeExecutionResult:
    """消費至多一筆pending request；DB claim永遠早於browser相關資源。"""

    if admission_controller.runtime_gate.browser_io_is_poisoned():
        return FacebookManualProbeExecutionResult(
            outcome=FacebookManualProbeOutcome.INCONCLUSIVE,
            failure_reason=SCHEDULER_RUNTIME_REASON,
            failure_stage=FacebookProbeFailureStage.RESOURCE_ACQUIRE,
        )
    prepared = _prepare_manual_probe_claim(
        db_path=options.db_path,
        profile_scope_key=profile_scope_key,
        clock=clock,
    )
    if isinstance(prepared, FacebookManualProbeExecutionResult):
        return prepared
    deadline = build_facebook_probe_deadline(
        configured_timeout_seconds=min(
            prepared.recipe.absolute_deadline_seconds,
            max(options.scan_timeout_seconds, 1.0),
        ),
        lease_expires_at=prepared.circuit.half_open_lease_expires_at,
        wall_now=clock(),
    )
    execution = await _run_claimed_manual_probe(
        claim=prepared,
        deadline=deadline,
        options=options,
        coordinator=coordinator,
        admission_controller=admission_controller,
        session_guard_runtime=session_guard_runtime,
        should_stop=should_stop,
        clock=clock,
    )
    finish = _finish_claimed_manual_probe(
        claim=prepared,
        execution=execution,
        db_path=options.db_path,
        profile_scope_key=profile_scope_key,
        session_guard_runtime=session_guard_runtime,
        finished_at=clock(),
    )
    if execution.propagate_cancel:
        raise asyncio.CancelledError
    result = _manual_probe_execution_result(prepared, execution, finish)
    _log_manual_probe_failure(result)
    return result


def _prepare_manual_probe_claim(
    *,
    db_path: Path,
    profile_scope_key: str,
    clock: Callable[[], datetime],
) -> _ClaimedManualProbe | FacebookManualProbeExecutionResult:
    """驗證 pending recipe 並在任何 browser 資源前完成 DB claim。"""

    pending = _load_pending_circuit(db_path, profile_scope_key)
    if pending is None or not pending.probe_request_id:
        return FacebookManualProbeExecutionResult(
            outcome=FacebookManualProbeOutcome.NO_REQUEST
        )
    request_id = pending.probe_request_id
    recipe = _APPROVED_RECIPES.get(pending.requested_recipe_kind)
    if recipe is None or pending.operation_kind != recipe.operation_kind:
        return FacebookManualProbeExecutionResult(
            outcome=FacebookManualProbeOutcome.RECIPE_UNAVAILABLE,
            request_id=request_id,
        )

    claim = _claim_probe(
        db_path,
        profile_scope_key,
        request_id=request_id,
        claimed_at=clock(),
    )
    if claim.outcome != FacebookHalfOpenClaimOutcome.CLAIMED or claim.state is None:
        return FacebookManualProbeExecutionResult(
            outcome=FacebookManualProbeOutcome.CLAIM_REJECTED,
            request_id=request_id,
        )
    return _ClaimedManualProbe(
        request_id=request_id,
        circuit=claim.state,
        target_id=claim.target_id,
        initial_target=_load_probe_target(db_path, claim.target_id),
        recipe=recipe,
    )


async def _run_claimed_manual_probe(
    *,
    claim: _ClaimedManualProbe,
    deadline: FacebookProbeDeadline,
    options: ResidentRuntimeOptions,
    coordinator: FacebookAutomationCoordinator,
    admission_controller: FacebookAutomationAdmissionController,
    session_guard_runtime: FacebookAutomationSessionGuardRuntime,
    should_stop: Callable[[], bool],
    clock: Callable[[], datetime],
) -> _ManualProbeResourceState:
    """執行 claim 後資源流程，並保證 pacing/coordinator owner 都被釋放。"""

    execution = _ManualProbeResourceState()
    stop_monitor_task = asyncio.create_task(
        _cancel_coordinator_waiters_when_stopping(
            coordinator=coordinator,
            should_stop=should_stop,
        ),
        name="manual-probe-stop-monitor",
    )

    try:
        await _execute_claimed_probe_resources(
            claim=claim,
            execution=execution,
            deadline=deadline,
            options=options,
            coordinator=coordinator,
            admission_controller=admission_controller,
            session_guard_runtime=session_guard_runtime,
            should_stop=should_stop,
            clock=clock,
        )
    except TimeoutError:
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE
        execution.failure_reason = SCAN_TIMEOUT_REASON
        execution.failure_stage = FacebookProbeFailureStage.DEADLINE
        execution.propagate_cancel = False
    except FacebookAutomationWaitCancelled:
        execution.cancel()
    except asyncio.CancelledError:
        execution.cancel(propagate=True)
    except ProfileLeaseError:
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE
        execution.failure_reason = PROFILE_LOCKED_REASON
        execution.failure_stage = FacebookProbeFailureStage.RESOURCE_ACQUIRE
    except Exception:
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE
        execution.failure_reason = UNKNOWN_REASON
        execution.failure_stage = FacebookProbeFailureStage.RESOURCE_ACQUIRE
    finally:
        await _release_manual_probe_resources(
            execution=execution,
            admission_controller=admission_controller,
            stop_monitor_task=stop_monitor_task,
            clock=clock,
        )
    return execution


async def _execute_claimed_probe_resources(
    *,
    claim: _ClaimedManualProbe,
    execution: _ManualProbeResourceState,
    deadline: FacebookProbeDeadline,
    options: ResidentRuntimeOptions,
    coordinator: FacebookAutomationCoordinator,
    admission_controller: FacebookAutomationAdmissionController,
    session_guard_runtime: FacebookAutomationSessionGuardRuntime,
    should_stop: Callable[[], bool],
    clock: Callable[[], datetime],
) -> None:
    """依既有順序取得 coordinator、pacing、profile lease 與 browser。"""

    if should_stop():
        execution.cancel()
        return
    if not _target_is_eligible(claim.initial_target, claim.recipe.operation_kind):
        return
    async with deadline.enforce():
        execution.process_lease = await coordinator.acquire(
            FacebookAutomationWorkKind.HALF_OPEN_PROBE,
            owner_alias="manual-half-open-probe",
        )
    if should_stop():
        execution.cancel()
        return
    browser_session_id = admission_controller.rotate_owner_session_id()
    async with deadline.enforce():
        await admission_controller.recover_expired_pacing_lease()
        await admission_controller.wait_until_pacing_available(
            process_lease=execution.process_lease
        )
    pacing = admission_controller.try_acquire_half_open_probe_pacing(
        operation_id=execution.process_lease.operation_id,
        started_at=clock(),
    )
    if pacing.outcome == FacebookPacingAcquireOutcome.ACQUIRED:
        execution.pacing_token = pacing.token
    if execution.pacing_token is None or should_stop():
        execution.cancel()
        return
    with acquire_profile_lease(options.profile_dir, "manual half-open probe"):
        if should_stop():
            execution.cancel()
            return
        current_target = _load_probe_target(options.db_path, claim.target_id)
        if not _target_is_eligible(current_target, claim.recipe.operation_kind):
            return
        if current_target is None:
            raise AssertionError("eligible manual probe target disappeared")
        session_guard_runtime.start_before_browser_io(
            started_at=clock(),
            session_id=browser_session_id,
        )
        try:
            observation = await execute_group_document_probe(
                options=options,
                target=current_target,
                recipe=claim.recipe,
                deadline=deadline,
                mark_trip_pending=lambda: _mark_manual_probe_trip_pending(
                    session_guard_runtime=session_guard_runtime,
                    recipe=claim.recipe,
                ),
                playwright_factory=async_playwright,
                launch_context=launch_persistent_context_async,
                page_guard=ensure_async_page_scannable,
            )
        except FacebookProbeBrowserCancelled as exc:
            _apply_manual_browser_observation(
                execution=execution,
                observation=exc.execution,
                admission_controller=admission_controller,
                coordinator=coordinator,
            )
            raise
        _apply_manual_browser_observation(
            execution=execution,
            observation=observation,
            admission_controller=admission_controller,
            coordinator=coordinator,
        )


def _apply_manual_browser_observation(
    *,
    execution: _ManualProbeResourceState,
    observation: FacebookProbeBrowserExecution,
    admission_controller: FacebookAutomationAdmissionController,
    coordinator: FacebookAutomationCoordinator,
) -> None:
    """套用 observation；未確認 cleanup 完成時永久隔離本 process。"""

    execution.apply_browser_observation(observation)
    if not observation.cleanup_completed:
        admission_controller.runtime_gate.poison_browser_io()
        coordinator.cancel_pending_waiters()


def _mark_manual_probe_trip_pending(
    *,
    session_guard_runtime: FacebookAutomationSessionGuardRuntime,
    recipe: FacebookRecoveryProbeRecipe,
) -> bool:
    """把 temporary-block observation 寫入 process marker。"""

    try:
        session_guard_runtime.mark_trip_pending(
            operation_kind=recipe.operation_kind,
            trigger_action_kind=recipe.action_kind,
        )
    except FacebookAutomationSessionGuardError:
        return False
    return True


async def _release_manual_probe_resources(
    *,
    execution: _ManualProbeResourceState,
    admission_controller: FacebookAutomationAdmissionController,
    stop_monitor_task: asyncio.Task[None],
    clock: Callable[[], datetime],
) -> None:
    """先收斂 cancellation checkpoint，再以最後結果完成 pacing owner。"""

    try:
        if execution.process_lease is not None:
            try:
                await execution.process_lease.release()
            except asyncio.CancelledError:
                execution.cancel(propagate=True)
    finally:
        try:
            monitor_cancelled = await cancel_probe_monitor_task(stop_monitor_task)
            if monitor_cancelled:
                execution.cancel(propagate=True)
        finally:
            if execution.pacing_token is not None:
                admission_controller.finish_half_open_probe_pacing(
                    execution.pacing_token,
                    outcome=execution.observed_result.value,
                    finished_at=clock(),
                )


def _finish_claimed_manual_probe(
    *,
    claim: _ClaimedManualProbe,
    execution: _ManualProbeResourceState,
    db_path: Path,
    profile_scope_key: str,
    session_guard_runtime: FacebookAutomationSessionGuardRuntime,
    finished_at: datetime,
) -> FacebookProbeFinishResult:
    """先 durable finish，再依 marker/context 狀態完成 sentinel acknowledgement。"""

    finish = _finish_probe(
        db_path,
        profile_scope_key,
        half_open_token=claim.circuit.half_open_token,
        generation=claim.circuit.generation,
        result=execution.observed_result,
        session_failure_reason=execution.failure_reason,
        finished_at=finished_at,
    )
    if (
        execution.marker_trip_pending
        and finish.outcome == FacebookProbeFinishOutcome.UPDATED
    ):
        session_guard_runtime.note_incident_committed()
    if execution.context_closed:
        session_guard_runtime.finish_after_browser_context_closed()
    return finish


def _manual_probe_execution_result(
    claim: _ClaimedManualProbe,
    execution: _ManualProbeResourceState,
    finish: FacebookProbeFinishResult,
) -> FacebookManualProbeExecutionResult:
    """將 durable finish 與 observation 映射成 public stable outcome。"""

    if finish.outcome != FacebookProbeFinishOutcome.UPDATED:
        outcome = FacebookManualProbeOutcome.STALE_OWNER
    else:
        outcome = {
            FacebookProbeResult.SUCCESS: FacebookManualProbeOutcome.SUCCEEDED,
            FacebookProbeResult.BLOCKED: FacebookManualProbeOutcome.BLOCKED,
            FacebookProbeResult.INCONCLUSIVE: FacebookManualProbeOutcome.INCONCLUSIVE,
            FacebookProbeResult.CANCELLED: FacebookManualProbeOutcome.CANCELLED,
        }[execution.observed_result]
    return FacebookManualProbeExecutionResult(
        outcome=outcome,
        request_id=claim.request_id,
        claimed=True,
        browser_launched=execution.browser_launched,
        page_count=execution.page_count,
        document_navigation_count=execution.document_count,
        context_closed=execution.context_closed,
        cleanup_completed=execution.cleanup_completed,
        finish_outcome=finish.outcome,
        failure_reason=execution.failure_reason,
        failure_stage=execution.failure_stage,
    )


def _log_manual_probe_failure(result: FacebookManualProbeExecutionResult) -> None:
    """只以固定 enum/reason 寫入可供 support bundle 聚合的安全事件。"""

    if result.failure_stage is None or not result.failure_reason:
        return
    logger.warning(
        "facebook_probe_failure probe=manual stage=%s reason=%s",
        result.failure_stage.value,
        result.failure_reason,
    )


async def _cancel_coordinator_waiters_when_stopping(
    *,
    coordinator: FacebookAutomationCoordinator,
    should_stop: Callable[[], bool],
) -> None:
    """在 manual probe 等待 coordinator/pacing 時將 runtime stop 轉為取消。"""

    while not should_stop():
        await asyncio.sleep(0.01)
    coordinator.cancel_pending_waiters()


def _load_pending_circuit(
    db_path: Path,
    profile_scope_key: str,
) -> FacebookAccessCircuitSnapshot | None:
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_access_circuit.get(profile_scope_key)
    if state is None or state.status != FacebookAccessCircuitStatus.OPEN:
        return None
    return state


def _claim_probe(
    db_path: Path,
    profile_scope_key: str,
    *,
    request_id: str,
    claimed_at: datetime,
) -> Any:
    with SqliteApplicationContext(db_path) as app:
        return app.services.facebook_access_circuit.claim_half_open(
            profile_scope_key,
            request_id=request_id,
            started_at=claimed_at,
        )


def _finish_probe(
    db_path: Path,
    profile_scope_key: str,
    *,
    half_open_token: str,
    generation: int,
    result: FacebookProbeResult,
    session_failure_reason: str,
    finished_at: datetime,
) -> FacebookProbeFinishResult:
    with SqliteApplicationContext(db_path) as app:
        finish = app.services.facebook_access_circuit.finish_probe(
            profile_scope_key,
            half_open_token=half_open_token,
            generation=generation,
            result=result,
            finished_at=finished_at,
        )
        if (
            finish.outcome == FacebookProbeFinishOutcome.UPDATED
            and session_failure_reason in PROFILE_SESSION_FAILURE_REASONS
        ):
            app.repositories.app_settings.mark_profile_needs_login(
                reason=session_failure_reason,
                source="facebook_access_manual_probe",
            )
        return finish


def _load_probe_target(db_path: Path, target_id: str | None) -> TargetDescriptor | None:
    normalized_target_id = str(target_id or "").strip()
    if not normalized_target_id:
        return None
    with SqliteApplicationContext(db_path) as app:
        return app.repositories.targets.get(normalized_target_id)


def _target_is_eligible(
    target: TargetDescriptor | None,
    operation_kind: FacebookProductOperationKind,
) -> bool:
    if target is None or not target.enabled or target.paused or not target.group_id.strip():
        return False
    if operation_kind == FacebookProductOperationKind.POSTS_ACCESS:
        return target.target_kind == TargetKind.POSTS
    return operation_kind in {
        FacebookProductOperationKind.GROUP_METADATA_ACCESS,
        FacebookProductOperationKind.COVER_METADATA_ACCESS,
    }


__all__ = [
    "FacebookManualProbeExecutionResult",
    "FacebookManualProbeOutcome",
    "consume_pending_facebook_manual_probe",
]
