"""Stale normal-session healthcheck 的 outer-supervisor executor。

職責：先 claim durable recovery owner，再依序取得 process coordinator、
pacing、profile lease 與單一 browser context。Probe 只讀取 group document
並執行 page guard，不寫 scan/latest/seen/history/outbox 等產品狀態。
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
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookAdmissionOutcome
from facebook_monitor.core.facebook_access import FacebookCircuitTripOutcome
from facebook_monitor.core.facebook_access import FacebookProbeFailureStage
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.facebook_automation_pacing import FacebookAutomationPacingToken
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryFinishOutcome,
)
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoverySnapshot
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryStatus,
)
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PROFILE_SESSION_FAILURE_REASONS
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON
from facebook_monitor.worker.facebook_access_runtime_gate import FacebookAccessRuntimeGate
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationLease,
)
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
    FacebookAutomationSessionGuardStore,
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
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookProbeBrowserCancelled,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookProbeBrowserExecution,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookProbeDeadline,
)
from facebook_monitor.worker.facebook_recovery_probe_runtime import (
    FacebookRecoveryProbeRecipe,
)
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.scan_orchestration import ensure_async_page_scannable


logger = logging.getLogger(__name__)


class FacebookSessionRecoveryProbeOutcome(StrEnum):
    """Outer supervisor 消化 recovery request 的 stable 結果。"""

    NO_REQUEST = "no_request"
    RECIPE_UNAVAILABLE = "recipe_unavailable"
    CLAIM_REJECTED = "claim_rejected"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"
    STALE_OWNER = "stale_owner"
    STORAGE_CRITICAL = "storage_critical"


@dataclass(frozen=True)
class FacebookSessionRecoveryProbeExecutionResult:
    """Probe 執行結果；不含 profile、URL 或 owner token。"""

    outcome: FacebookSessionRecoveryProbeOutcome
    claimed: bool = False
    browser_launched: bool = False
    page_count: int = 0
    document_navigation_count: int = 0
    context_closed: bool = False
    cleanup_completed: bool = True
    marker_cleared: bool = False
    failure_reason: str = ""
    failure_stage: FacebookProbeFailureStage | None = None


@dataclass(frozen=True)
class _ClaimedProbe:
    """保存 durable claim 後不可再由 runtime 猜測的 probe 輸入。"""

    state: FacebookSessionRecoverySnapshot
    target_id: str | None
    target: TargetDescriptor | None
    recipe: FacebookRecoveryProbeRecipe


@dataclass(frozen=True)
class _ProbeClaimPreflight:
    """保存 claim/preflight 的 early result 或 claimed owner。"""

    claimed: _ClaimedProbe | None = None
    early_result: FacebookSessionRecoveryProbeExecutionResult | None = None


@dataclass
class _ProbeExecutionState:
    """保存 claimed probe 的 process resource 與 finish 狀態。"""

    observed_result: FacebookProbeResult = FacebookProbeResult.INCONCLUSIVE
    browser_launched: bool = False
    page_count: int = 0
    document_count: int = 0
    context_closed: bool = False
    cleanup_completed: bool = True
    marker_trip_pending: bool = False
    marker_cleared: bool = False
    failure_reason: str = ""
    failure_stage: FacebookProbeFailureStage | None = None
    propagate_cancel: bool = False
    process_lease: FacebookAutomationLease | None = None
    pacing_token: FacebookAutomationPacingToken | None = None
    finish_outcome: FacebookSessionRecoveryFinishOutcome | None = None

    def apply_browser_execution(self, execution: FacebookProbeBrowserExecution) -> None:
        """套用 browser helper 結果，不改 durable finish 狀態。"""

        self.observed_result = execution.result
        self.browser_launched = execution.browser_launched
        self.page_count = execution.page_count
        self.document_count = execution.document_count
        self.context_closed = execution.context_closed
        self.cleanup_completed = execution.cleanup_completed
        self.marker_trip_pending = execution.marker_trip_pending
        self.failure_reason = execution.failure_reason
        self.failure_stage = execution.failure_stage
        self.propagate_cancel = execution.cancelled


_APPROVED_RECIPES = APPROVED_FACEBOOK_RECOVERY_PROBE_RECIPES


async def consume_pending_facebook_session_recovery_probe(
    *,
    options: ResidentRuntimeOptions,
    profile_scope_key: str,
    coordinator: FacebookAutomationCoordinator,
    admission_controller: FacebookAutomationAdmissionController,
    runtime_gate: FacebookAccessRuntimeGate,
    session_guard_store: FacebookAutomationSessionGuardStore,
    should_stop: Callable[[], bool],
    clock: Callable[[], datetime] = utc_now,
) -> FacebookSessionRecoveryProbeExecutionResult:
    """Claim 並消化至多一筆 stale-session probe request。"""

    if runtime_gate.browser_io_is_poisoned():
        return FacebookSessionRecoveryProbeExecutionResult(
            outcome=FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE,
            failure_reason=SCHEDULER_RUNTIME_REASON,
            failure_stage=FacebookProbeFailureStage.RESOURCE_ACQUIRE,
        )
    preflight = _claim_pending_probe(
        db_path=options.db_path,
        profile_scope_key=profile_scope_key,
        claimed_at=clock(),
    )
    if preflight.early_result is not None:
        return preflight.early_result
    claimed = preflight.claimed
    if claimed is None:
        raise AssertionError("session recovery preflight returned no claimed probe")
    deadline = build_facebook_probe_deadline(
        configured_timeout_seconds=min(
            claimed.recipe.absolute_deadline_seconds,
            max(options.scan_timeout_seconds, 1.0),
        ),
        lease_expires_at=claimed.state.probe_lease_expires_at,
        wall_now=clock(),
    )

    execution = _ProbeExecutionState()
    stop_monitor_task = asyncio.create_task(
        _cancel_coordinator_waiters_when_stopping(
            coordinator=coordinator,
            should_stop=should_stop,
        ),
        name="session-recovery-probe-stop-monitor",
    )

    resource_phase = True
    try:
        await _run_claimed_probe_resources(
            options=options,
            claimed=claimed,
            execution=execution,
            deadline=deadline,
            coordinator=coordinator,
            admission_controller=admission_controller,
            runtime_gate=runtime_gate,
            session_guard_store=session_guard_store,
            should_stop=should_stop,
            clock=clock,
        )
        resource_phase = False
    except TimeoutError:
        execution.failure_reason = SCAN_TIMEOUT_REASON
        execution.failure_stage = FacebookProbeFailureStage.DEADLINE
        execution.propagate_cancel = False
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE
    except FacebookAutomationWaitCancelled:
        execution.observed_result = FacebookProbeResult.CANCELLED
    except asyncio.CancelledError:
        execution.propagate_cancel = True
        execution.observed_result = FacebookProbeResult.CANCELLED
    except ProfileLeaseError:
        execution.failure_reason = PROFILE_LOCKED_REASON
        execution.failure_stage = FacebookProbeFailureStage.RESOURCE_ACQUIRE
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE
    except Exception:
        if resource_phase and not execution.failure_reason:
            execution.failure_reason = UNKNOWN_REASON
            execution.failure_stage = FacebookProbeFailureStage.RESOURCE_ACQUIRE
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE
    finally:
        _linearize_confirmed_block_before_release(
            options=options,
            profile_scope_key=profile_scope_key,
            claimed=claimed,
            execution=execution,
            runtime_gate=runtime_gate,
            session_guard_store=session_guard_store,
            clock=clock,
        )
        await _release_probe_resources(
            execution=execution,
            admission_controller=admission_controller,
            stop_monitor_task=stop_monitor_task,
            clock=clock,
        )

    if execution.propagate_cancel and execution.finish_outcome is None:
        execution.observed_result = FacebookProbeResult.CANCELLED
    try:
        _finish_claimed_probe(
            options=options,
            profile_scope_key=profile_scope_key,
            claimed=claimed,
            execution=execution,
            runtime_gate=runtime_gate,
            session_guard_store=session_guard_store,
            clock=clock,
        )
    except Exception:
        _finish_probe_after_exception(
            db_path=options.db_path,
            profile_scope_key=profile_scope_key,
            claimed=claimed,
            execution=execution,
            observed_result=FacebookProbeResult.INCONCLUSIVE,
            clock=clock,
        )

    result = _build_probe_execution_result(execution)
    _log_session_recovery_probe_failure(result)
    if execution.propagate_cancel:
        raise asyncio.CancelledError
    return result


def _linearize_confirmed_block_before_release(
    *,
    options: ResidentRuntimeOptions,
    profile_scope_key: str,
    claimed: _ClaimedProbe,
    execution: _ProbeExecutionState,
    runtime_gate: FacebookAccessRuntimeGate,
    session_guard_store: FacebookAutomationSessionGuardStore,
    clock: Callable[[], datetime],
) -> None:
    """持有 coordinator owner 時先把 confirmed block 收斂成不可逆安全狀態。"""

    if (
        execution.observed_result != FacebookProbeResult.BLOCKED
        or not execution.marker_trip_pending
        or execution.process_lease is None
    ):
        return
    try:
        _finish_claimed_probe(
            options=options,
            profile_scope_key=profile_scope_key,
            claimed=claimed,
            execution=execution,
            runtime_gate=runtime_gate,
            session_guard_store=session_guard_store,
            clock=clock,
        )
    except Exception:
        # 保留 runtime gate 的 fail-closed 狀態；lease 仍必須在 finally 釋放。
        execution.observed_result = FacebookProbeResult.INCONCLUSIVE


def _claim_pending_probe(
    *,
    db_path: Path,
    profile_scope_key: str,
    claimed_at: datetime,
) -> _ProbeClaimPreflight:
    """讀取、驗證 recipe 並 claim 一筆 durable recovery request。"""

    pending = _load_pending_recovery(db_path, profile_scope_key)
    if pending is None or not pending.request_id:
        return _early_probe_result(FacebookSessionRecoveryProbeOutcome.NO_REQUEST)
    recipe = _APPROVED_RECIPES.get(pending.requested_recipe_kind)
    if recipe is None or pending.requested_operation_kind != recipe.operation_kind:
        return _early_probe_result(
            FacebookSessionRecoveryProbeOutcome.RECIPE_UNAVAILABLE
        )
    claim = _claim_probe(
        db_path,
        profile_scope_key,
        request_id=pending.request_id,
        claimed_at=claimed_at,
    )
    if (
        claim.outcome != FacebookSessionRecoveryClaimOutcome.CLAIMED
        or claim.state is None
    ):
        return _early_probe_result(FacebookSessionRecoveryProbeOutcome.CLAIM_REJECTED)
    return _ProbeClaimPreflight(
        claimed=_ClaimedProbe(
            state=claim.state,
            target_id=claim.target_id,
            target=_load_probe_target(db_path, claim.target_id),
            recipe=recipe,
        )
    )


def _early_probe_result(outcome: FacebookSessionRecoveryProbeOutcome) -> _ProbeClaimPreflight:
    """建立 claim 前即可回傳的 bounded execution result。"""

    return _ProbeClaimPreflight(
        early_result=FacebookSessionRecoveryProbeExecutionResult(outcome)
    )


async def _run_claimed_probe_resources(
    *,
    options: ResidentRuntimeOptions,
    claimed: _ClaimedProbe,
    execution: _ProbeExecutionState,
    deadline: FacebookProbeDeadline,
    coordinator: FacebookAutomationCoordinator,
    admission_controller: FacebookAutomationAdmissionController,
    runtime_gate: FacebookAccessRuntimeGate,
    session_guard_store: FacebookAutomationSessionGuardStore,
    should_stop: Callable[[], bool],
    clock: Callable[[], datetime],
) -> None:
    """依序取得 coordinator、pacing、profile 與 browser resources。"""

    if should_stop():
        execution.observed_result = FacebookProbeResult.CANCELLED
        return
    if not _target_is_eligible(claimed.target, claimed.recipe.operation_kind):
        return
    if claimed.target is None:
        raise AssertionError("eligible session recovery target disappeared")

    async with deadline.enforce():
        execution.process_lease = await coordinator.acquire(
            FacebookAutomationWorkKind.HALF_OPEN_PROBE,
            owner_alias="stale-session-recovery-probe",
        )
    if should_stop():
        execution.observed_result = FacebookProbeResult.CANCELLED
        return
    execution.pacing_token = await _acquire_probe_pacing(
        claimed=claimed,
        process_lease=execution.process_lease,
        admission_controller=admission_controller,
        deadline=deadline,
        clock=clock,
    )
    if execution.pacing_token is None or should_stop():
        execution.observed_result = FacebookProbeResult.CANCELLED
        return

    with acquire_profile_lease(options.profile_dir, "stale session recovery probe"):
        if should_stop():
            execution.observed_result = FacebookProbeResult.CANCELLED
            return
        current_target = _load_probe_target(options.db_path, claimed.target_id)
        if not _target_is_eligible(current_target, claimed.recipe.operation_kind):
            return
        if current_target is None:
            raise AssertionError("eligible session recovery target disappeared")
        try:
            browser_execution = await execute_group_document_probe(
                options=options,
                target=current_target,
                recipe=claimed.recipe,
                deadline=deadline,
                mark_trip_pending=lambda: _mark_session_probe_trip_pending(
                    session_guard_store=session_guard_store,
                    marker_session_id=claimed.state.marker_session_id,
                    recipe=claimed.recipe,
                ),
                playwright_factory=async_playwright,
                launch_context=launch_persistent_context_async,
                page_guard=ensure_async_page_scannable,
            )
        except FacebookProbeBrowserCancelled as exc:
            _apply_session_browser_execution(
                execution=execution,
                observation=exc.execution,
                runtime_gate=runtime_gate,
                coordinator=coordinator,
            )
            raise
        _apply_session_browser_execution(
            execution=execution,
            observation=browser_execution,
            runtime_gate=runtime_gate,
            coordinator=coordinator,
        )


def _apply_session_browser_execution(
    *,
    execution: _ProbeExecutionState,
    observation: FacebookProbeBrowserExecution,
    runtime_gate: FacebookAccessRuntimeGate,
    coordinator: FacebookAutomationCoordinator,
) -> None:
    """套用 observation；未確認 cleanup 完成時永久隔離本 process。"""

    execution.apply_browser_execution(observation)
    if not observation.cleanup_completed:
        runtime_gate.poison_browser_io()
        coordinator.cancel_pending_waiters()


def _mark_session_probe_trip_pending(
    *,
    session_guard_store: FacebookAutomationSessionGuardStore,
    marker_session_id: str,
    recipe: FacebookRecoveryProbeRecipe,
) -> bool:
    """把 temporary-block observation 寫入 matching stale marker。"""

    try:
        session_guard_store.mark_trip_pending(
            session_id=marker_session_id,
            operation_kind=recipe.operation_kind,
            trigger_action_kind=recipe.action_kind,
        )
    except FacebookAutomationSessionGuardError:
        return False
    return True


async def _acquire_probe_pacing(
    *,
    claimed: _ClaimedProbe,
    process_lease: FacebookAutomationLease,
    admission_controller: FacebookAutomationAdmissionController,
    deadline: FacebookProbeDeadline,
    clock: Callable[[], datetime],
) -> FacebookAutomationPacingToken | None:
    """等待 persistent quiet gap 並取得單次 half-open pacing owner。"""

    async with deadline.enforce():
        await admission_controller.recover_expired_pacing_lease()
        await admission_controller.wait_until_pacing_available(
            process_lease=process_lease
        )
    pacing = admission_controller.try_acquire_half_open_probe_pacing(
        operation_id=process_lease.operation_id,
        started_at=clock(),
        owner_session_id=claimed.state.marker_session_id,
    )
    if (
        pacing.outcome != FacebookPacingAcquireOutcome.ACQUIRED
        or pacing.token is None
    ):
        return None
    return pacing.token


def _finish_probe_pacing(
    *,
    execution: _ProbeExecutionState,
    admission_controller: FacebookAutomationAdmissionController,
    clock: Callable[[], datetime],
) -> None:
    """完成 pacing owner；成功後清除 process 內 token 避免 finally 重複。"""

    if execution.pacing_token is None:
        return
    admission_controller.finish_half_open_probe_pacing(
        execution.pacing_token,
        outcome=execution.observed_result.value,
        finished_at=clock(),
    )
    execution.pacing_token = None


def _finish_claimed_probe(
    *,
    options: ResidentRuntimeOptions,
    profile_scope_key: str,
    claimed: _ClaimedProbe,
    execution: _ProbeExecutionState,
    runtime_gate: FacebookAccessRuntimeGate,
    session_guard_store: FacebookAutomationSessionGuardStore,
    clock: Callable[[], datetime],
) -> None:
    """依 observed result 完成 durable owner，成功後才依 context 狀態清 marker。"""

    if execution.finish_outcome is not None:
        return
    if execution.observed_result == FacebookProbeResult.BLOCKED:
        if not execution.marker_trip_pending or execution.process_lease is None:
            execution.observed_result = FacebookProbeResult.INCONCLUSIVE
        else:
            execution.finish_outcome = _finish_blocked_probe(
                options.db_path,
                profile_scope_key,
                generation=claimed.state.generation,
                probe_token=claimed.state.probe_token,
                target_id=claimed.target_id,
                recipe=claimed.recipe,
                source_owner_token=execution.process_lease.operation_id,
                runtime_gate=runtime_gate,
                finished_at=clock(),
            )
    if execution.finish_outcome is None:
        execution.finish_outcome = _finish_probe(
            options.db_path,
            profile_scope_key,
            generation=claimed.state.generation,
            probe_token=claimed.state.probe_token,
            result=execution.observed_result,
            session_failure_reason=execution.failure_reason,
            finished_at=clock(),
        )
    _clear_finished_probe_marker(
        execution=execution,
        claimed=claimed,
        session_guard_store=session_guard_store,
    )


def _clear_finished_probe_marker(
    *,
    execution: _ProbeExecutionState,
    claimed: _ClaimedProbe,
    session_guard_store: FacebookAutomationSessionGuardStore,
) -> None:
    """只有 durable finish 更新且 context 已關閉時才清 matching marker。"""

    if (
        execution.finish_outcome != FacebookSessionRecoveryFinishOutcome.UPDATED
        or not execution.context_closed
    ):
        return
    if execution.observed_result == FacebookProbeResult.SUCCESS:
        execution.marker_cleared = _clear_recovered_normal_marker(
            session_guard_store,
            marker_session_id=claimed.state.marker_session_id,
        )
    elif execution.observed_result == FacebookProbeResult.BLOCKED:
        execution.marker_cleared = _clear_durable_trip_marker(
            session_guard_store,
            marker_session_id=claimed.state.marker_session_id,
        )


def _finish_probe_after_exception(
    *,
    db_path: Path,
    profile_scope_key: str,
    claimed: _ClaimedProbe,
    execution: _ProbeExecutionState,
    observed_result: FacebookProbeResult,
    clock: Callable[[], datetime],
) -> None:
    """依原 owner token 把 wait/cancel/unexpected exception 收斂為 durable result。"""

    execution.observed_result = observed_result
    execution.finish_outcome = _finish_probe(
        db_path,
        profile_scope_key,
        generation=claimed.state.generation,
        probe_token=claimed.state.probe_token,
        result=execution.observed_result,
        session_failure_reason=execution.failure_reason,
        finished_at=clock(),
    )


async def _release_probe_resources(
    *,
    execution: _ProbeExecutionState,
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
                execution.propagate_cancel = True
                if execution.finish_outcome is None:
                    execution.observed_result = FacebookProbeResult.CANCELLED
    finally:
        try:
            monitor_cancelled = await cancel_probe_monitor_task(stop_monitor_task)
            if monitor_cancelled:
                execution.propagate_cancel = True
                if execution.finish_outcome is None:
                    execution.observed_result = FacebookProbeResult.CANCELLED
        finally:
            _finish_probe_pacing(
                execution=execution,
                admission_controller=admission_controller,
                clock=clock,
            )


def _build_probe_execution_result(
    execution: _ProbeExecutionState,
) -> FacebookSessionRecoveryProbeExecutionResult:
    """把 internal mutable state 投影為 bounded stable result。"""

    if execution.finish_outcome != FacebookSessionRecoveryFinishOutcome.UPDATED:
        outcome = FacebookSessionRecoveryProbeOutcome.STALE_OWNER
    elif execution.observed_result in {
        FacebookProbeResult.SUCCESS,
        FacebookProbeResult.BLOCKED,
    } and not execution.marker_cleared:
        outcome = FacebookSessionRecoveryProbeOutcome.STORAGE_CRITICAL
    else:
        outcome = _stable_probe_outcome(execution.observed_result)
    return FacebookSessionRecoveryProbeExecutionResult(
        outcome=outcome,
        claimed=True,
        browser_launched=execution.browser_launched,
        page_count=execution.page_count,
        document_navigation_count=execution.document_count,
        context_closed=execution.context_closed,
        cleanup_completed=execution.cleanup_completed,
        marker_cleared=execution.marker_cleared,
        failure_reason=execution.failure_reason,
        failure_stage=execution.failure_stage,
    )


def _log_session_recovery_probe_failure(
    result: FacebookSessionRecoveryProbeExecutionResult,
) -> None:
    """只以固定 enum/reason 寫入可供 support bundle 聚合的安全事件。"""

    if result.failure_stage is None or not result.failure_reason:
        return
    logger.warning(
        "facebook_probe_failure probe=session_recovery stage=%s reason=%s",
        result.failure_stage.value,
        result.failure_reason,
    )


def _stable_probe_outcome(
    result: FacebookProbeResult,
) -> FacebookSessionRecoveryProbeOutcome:
    """映射已 finish 的 probe result；NONE 不可能由 executor 產生。"""

    return {
        FacebookProbeResult.SUCCESS: FacebookSessionRecoveryProbeOutcome.SUCCEEDED,
        FacebookProbeResult.BLOCKED: FacebookSessionRecoveryProbeOutcome.BLOCKED,
        FacebookProbeResult.INCONCLUSIVE: FacebookSessionRecoveryProbeOutcome.INCONCLUSIVE,
        FacebookProbeResult.CANCELLED: FacebookSessionRecoveryProbeOutcome.CANCELLED,
    }[result]


def _load_pending_recovery(db_path: Path, profile_scope_key: str) -> Any | None:
    with SqliteApplicationContext(db_path) as app:
        state = app.services.facebook_session_recovery.get(profile_scope_key)
    if state is None or state.status != FacebookSessionRecoveryStatus.PROBE_PENDING:
        return None
    return state


def _claim_probe(
    db_path: Path,
    profile_scope_key: str,
    *,
    request_id: str,
    claimed_at: datetime,
) -> FacebookSessionRecoveryClaimResult:
    with SqliteApplicationContext(db_path) as app:
        return app.services.facebook_session_recovery.claim_probe(
            profile_scope_key,
            request_id=request_id,
            started_at=claimed_at,
        )


def _finish_probe(
    db_path: Path,
    profile_scope_key: str,
    *,
    generation: int,
    probe_token: str,
    result: FacebookProbeResult,
    session_failure_reason: str,
    finished_at: datetime,
) -> FacebookSessionRecoveryFinishOutcome:
    with SqliteApplicationContext(db_path) as app:
        finish = app.services.facebook_session_recovery.finish_probe(
            profile_scope_key,
            generation=generation,
            probe_token=probe_token,
            result=result,
            finished_at=finished_at,
        )
        if (
            finish.outcome == FacebookSessionRecoveryFinishOutcome.UPDATED
            and session_failure_reason in PROFILE_SESSION_FAILURE_REASONS
        ):
            app.repositories.app_settings.mark_profile_needs_login(
                reason=session_failure_reason,
                source="facebook_session_recovery_probe",
            )
        return finish.outcome


def _finish_blocked_probe(
    db_path: Path,
    profile_scope_key: str,
    *,
    generation: int,
    probe_token: str,
    target_id: str | None,
    recipe: FacebookRecoveryProbeRecipe,
    source_owner_token: str,
    runtime_gate: FacebookAccessRuntimeGate,
    finished_at: datetime,
) -> FacebookSessionRecoveryFinishOutcome:
    """在同一 transaction 開啟 access circuit 並收旂 session probe owner。"""

    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        connection = app.repositories.facebook_session_recovery.connection
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        access_state = app.services.facebook_access_circuit.get(profile_scope_key)
        if access_state is None or access_state.status == FacebookAccessCircuitStatus.CLOSED:
            admission = app.services.facebook_access_circuit.admit_normal(
                profile_scope_key,
                process_safety_epoch=runtime_gate.current_safety_epoch(),
                operation_id=source_owner_token,
                admitted_at=finished_at,
            )
            if (
                admission.outcome != FacebookAdmissionOutcome.ALLOWED
                or admission.token is None
                or not runtime_gate.request_trip(admission.token)
            ):
                raise RuntimeError("session recovery block admission was rejected")
            trip = app.services.facebook_access_circuit.trip(
                FacebookAccessBlockSignal(
                    admission_token=admission.token,
                    source_kind=FacebookWorkSourceKind.PROBE,
                    operation_kind=recipe.operation_kind,
                    trigger_action_kind=recipe.action_kind,
                    source_owner_token=source_owner_token,
                    target_id=target_id,
                    evidence_code="facebook_session_recovery_probe_v1",
                ),
                source_owner_is_valid=True,
                detected_at=finished_at,
            )
            if trip.outcome not in {
                FacebookCircuitTripOutcome.OPENED,
                FacebookCircuitTripOutcome.REPEATED,
            }:
                raise RuntimeError("session recovery block circuit trip was rejected")
        elif access_state.status != FacebookAccessCircuitStatus.OPEN:
            raise RuntimeError("session recovery block found half-open circuit")
        finish = app.services.facebook_session_recovery.finish_probe(
            profile_scope_key,
            generation=generation,
            probe_token=probe_token,
            result=FacebookProbeResult.BLOCKED,
            finished_at=finished_at,
        )
        if finish.outcome != FacebookSessionRecoveryFinishOutcome.UPDATED:
            raise RuntimeError("session recovery block lost probe owner")
        return finish.outcome


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


def _clear_recovered_normal_marker(
    store: FacebookAutomationSessionGuardStore,
    *,
    marker_session_id: str,
) -> bool:
    try:
        store.clear_clean_session(session_id=marker_session_id)
    except FacebookAutomationSessionGuardError:
        return False
    return True


def _clear_durable_trip_marker(
    store: FacebookAutomationSessionGuardStore,
    *,
    marker_session_id: str,
) -> bool:
    try:
        store.clear_persisted_trip(session_id=marker_session_id)
    except FacebookAutomationSessionGuardError:
        return False
    return True


async def _cancel_coordinator_waiters_when_stopping(
    *,
    coordinator: FacebookAutomationCoordinator,
    should_stop: Callable[[], bool],
) -> None:
    """讓 coordinator/quiet-gap 等待可被 resident stop 取消。"""

    while not should_stop():
        await asyncio.sleep(0.01)
    coordinator.cancel_pending_waiters()


__all__ = [
    "FacebookSessionRecoveryProbeExecutionResult",
    "FacebookSessionRecoveryProbeOutcome",
    "consume_pending_facebook_session_recovery_probe",
]
