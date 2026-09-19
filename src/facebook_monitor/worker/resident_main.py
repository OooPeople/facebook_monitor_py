"""Formal resident main worker loop。

職責：正式產品主路徑，負責 Playwright persistent context 生命週期與
producer-only scheduler tick 接線；queue、page pool 與 executor worker pool
分別由專門模組承擔。fallback/debug path 不應反向牽動此主路徑。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Coroutine
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import logging
from pathlib import Path
import sqlite3
from typing import Any
from typing import Protocol

from playwright.async_api import async_playwright

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_async
from facebook_monitor.application.managed_profile_identity import (
    resolve_managed_profile_identity,
)
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.application.maintenance import run_bounded_retention_maintenance_for_db
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_FACEBOOK_AUTOMATION_DEFAULTS
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoverySnapshot
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_MISSING_REASON
from facebook_monitor.facebook.group_metadata import (
    AsyncBrowserContextLike as GroupMetadataBrowserContextLike,
)
from facebook_monitor.notifications.outbox_dispatcher import (
    wake_notification_outbox_dispatcher_for_db,
)
from facebook_monitor.notifications.outbox_dispatch_service import (
    dispatch_new_pending_notification_outbox_for_db,
)
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.runtime.paths import FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME
from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.scheduler.runtime_recovery import recover_stale_runtime_targets_detailed
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_access_runtime_gate import FacebookAccessRuntimeGate
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_access_manual_probe import (
    FacebookManualProbeOutcome,
)
from facebook_monitor.worker.facebook_access_manual_probe import (
    consume_pending_facebook_manual_probe,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardRuntime,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationRestartGuardOutcome,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationRestartGuardResult,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardStore,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    derive_facebook_automation_profile_alias,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    reconcile_facebook_automation_restart_guard,
)
from facebook_monitor.worker.facebook_session_recovery_probe import (
    consume_pending_facebook_session_recovery_probe,
)
from facebook_monitor.worker.facebook_page_lifecycle import (
    close_existing_context_pages_async,
)
from facebook_monitor.worker.posts_pipeline import scan_posts_page_async_commit_ready
import facebook_monitor.worker.resident_cover_image_refresh as resident_cover_image_refresh
import facebook_monitor.worker.resident_metadata_refresh as resident_metadata_refresh
import facebook_monitor.worker.resident_runtime_errors as resident_runtime_errors
from facebook_monitor.worker.resident_shared import ResidentCycleSummary
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_executor_types import AsyncCommitReadyScanCallable
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_recovery import ResidentRecoveryCoordinator


logger = logging.getLogger(__name__)
_DISPLAY_NEXT_DUE_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="facebook-monitor-display-next-due",
)
_DISPLAY_NEXT_DUE_BUSY_TIMEOUT_MS = 100
AsyncSleepCallable = Callable[[float], Coroutine[Any, Any, None]]
StopCheckCallable = Callable[[], bool]
AsyncCycleObserver = Callable[[ResidentCycleSummary], None]


class BrowserTimeoutContextLike(Protocol):
    """resident 啟動時設定 Playwright context timeout 需要的能力。"""

    def set_default_timeout(self, timeout: float) -> None:
        """設定 Playwright default timeout。"""

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """設定 Playwright navigation timeout。"""


@dataclass(frozen=True)
class ResidentRuntimeSessionResult:
    """保存單次 browser runtime session 結束原因與 cycle 進度。"""

    cycle_index: int
    runtime_restart_requested: bool


class ResidentSupervisorAction(StrEnum):
    """Outer supervisor 依 durable safety state 選擇的單輪動作。"""

    SESSION_RECOVERY_PROBE = "session_recovery_probe"
    MANUAL_ACCESS_PROBE = "manual_access_probe"
    DORMANT = "dormant"
    BROWSER_SESSION = "browser_session"


class ResidentSupervisorDisposition(StrEnum):
    """Outer supervisor 單輪完成後的 loop 轉移。"""

    CONTINUE = "continue"
    BREAK = "break"


@dataclass(frozen=True)
class ResidentSupervisorState:
    """保存單輪 browser-free safety/recovery 決策所需狀態。"""

    restart_guard: FacebookAutomationRestartGuardResult
    circuit_state: FacebookAccessCircuitSnapshot | None
    circuit_status: FacebookAccessCircuitStatus
    runtime_gate_closed: bool
    session_recovery_state: FacebookSessionRecoverySnapshot | None


@dataclass(frozen=True)
class ResidentSupervisorTransition:
    """保存 outer supervisor 下一輪的 cycle、guard 與控制轉移。"""

    cycle_index: int
    restart_guard: FacebookAutomationRestartGuardResult
    disposition: ResidentSupervisorDisposition


@dataclass(frozen=True)
class ResidentSupervisorRuntime:
    """集中 outer supervisor 不變的依賴，避免狀態流程散落於入口。"""

    options: ResidentRuntimeOptions
    scan_page: AsyncCommitReadyScanCallable
    comments_commit_ready_scan_page: AsyncCommitReadyScanCallable | None
    schedule_planner: TargetSchedulePlanner
    stop_requested: StopCheckCallable
    sleep: AsyncSleepCallable
    clock: Callable[[], datetime]
    on_cycle: AsyncCycleObserver | None
    summaries: list[ResidentCycleSummary]
    profile_scope_key: str
    session_guard_store: FacebookAutomationSessionGuardStore
    session_guard_runtime: FacebookAutomationSessionGuardRuntime
    automation_coordinator: FacebookAutomationCoordinator
    admission_controller: FacebookAutomationAdmissionController
    access_runtime_gate: FacebookAccessRuntimeGate


def _install_playwright_shutdown_exception_handler() -> Callable[[], None]:
    """安裝 resident worker 關閉期間用的 Playwright 例外過濾器。"""

    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_exception(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        """只消化 Playwright runtime shutdown 的已知背景 future 例外。"""

        if resident_runtime_errors.is_playwright_shutdown_noise_context(context):
            return
        if previous_handler is not None:
            previous_handler(loop, context)
            return
        loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)

    def restore_handler() -> None:
        """還原呼叫端原本的 event loop exception handler。"""

        loop.set_exception_handler(previous_handler)

    return restore_handler


async def run_resident_main_loop(
    options: ResidentRuntimeOptions,
    *,
    scan_page: AsyncCommitReadyScanCallable = scan_posts_page_async_commit_ready,
    comments_commit_ready_scan_page: AsyncCommitReadyScanCallable | None = None,
    sleep_fn: AsyncSleepCallable | None = None,
    automation_sleep_fn: AsyncSleepCallable | None = None,
    automation_clock: Callable[[], datetime] | None = None,
    should_stop: StopCheckCallable | None = None,
    on_cycle: AsyncCycleObserver | None = None,
) -> list[ResidentCycleSummary]:
    """執行 queue-based continuous resident main worker loop。"""

    if not options.profile_dir.exists():
        raise WorkerFailure(PROFILE_MISSING_REASON, str(options.profile_dir))
    _log_resident_main_start(options)
    runtime, restart_guard = _build_resident_supervisor_runtime(
        options=options,
        scan_page=scan_page,
        comments_commit_ready_scan_page=comments_commit_ready_scan_page,
        sleep_fn=sleep_fn,
        automation_sleep_fn=automation_sleep_fn,
        automation_clock=automation_clock,
        should_stop=should_stop,
        on_cycle=on_cycle,
    )
    transition = ResidentSupervisorTransition(
        cycle_index=0,
        restart_guard=restart_guard,
        disposition=ResidentSupervisorDisposition.CONTINUE,
    )

    restore_playwright_exception_handler = _install_playwright_shutdown_exception_handler()
    try:
        try:
            while _resident_supervisor_should_run(runtime, transition.cycle_index):
                transition = await _run_resident_supervisor_iteration(
                    runtime,
                    transition,
                )
                if transition.disposition == ResidentSupervisorDisposition.BREAK:
                    break
        except ProfileLeaseError as exc:
            raise WorkerFailure(PROFILE_LOCKED_REASON, str(exc)) from exc
    finally:
        restore_playwright_exception_handler()

    return runtime.summaries


def _log_resident_main_start(options: ResidentRuntimeOptions) -> None:
    """記錄 resident 啟動參數，不參與 supervisor 狀態轉移。"""

    logger.info(
        "resident_main_start db_path=%s profile_dir=%s interval_seconds=%s "
        "scheduler_tick_seconds=%s max_concurrent_scans=%s scan_timeout_seconds=%s "
        "stale_running_after_seconds=%s headed_compat=%s",
        options.db_path,
        options.profile_dir,
        options.interval_seconds,
        options.scheduler_tick_seconds,
        options.max_concurrent_scans,
        options.scan_timeout_seconds,
        options.stale_running_after_seconds,
        options.headed_compat,
    )


def _build_resident_supervisor_runtime(
    *,
    options: ResidentRuntimeOptions,
    scan_page: AsyncCommitReadyScanCallable,
    comments_commit_ready_scan_page: AsyncCommitReadyScanCallable | None,
    sleep_fn: AsyncSleepCallable | None,
    automation_sleep_fn: AsyncSleepCallable | None,
    automation_clock: Callable[[], datetime] | None,
    should_stop: StopCheckCallable | None,
    on_cycle: AsyncCycleObserver | None,
) -> tuple[ResidentSupervisorRuntime, FacebookAutomationRestartGuardResult]:
    """依原始順序建立 outer supervisor 依賴與初始 restart guard。"""

    summaries: list[ResidentCycleSummary] = []
    schedule_planner = TargetSchedulePlanner(
        scannable_target_kinds=frozenset({TargetKind.POSTS}),
        on_display_next_due_changed=_publish_display_next_due_at(options.db_path),
    )
    stop_requested = should_stop or (lambda: False)
    sleep = sleep_fn or asyncio.sleep
    automation_sleep = automation_sleep_fn or sleep
    clock = automation_clock or utc_now
    automation_coordinator = FacebookAutomationCoordinator(
        quiet_gap_min_seconds=(PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.quiet_gap_min_seconds),
        quiet_gap_max_seconds=(PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.quiet_gap_max_seconds),
        sleep_fn=automation_sleep,
    )
    profile_identity = resolve_managed_profile_identity(
        db_path=options.db_path,
        profiles_root=options.profile_dir.parent,
        profile_dir=options.profile_dir,
    )
    session_guard_store = _facebook_automation_session_guard_store(
        options,
        profile_scope_key=profile_identity.profile_scope_key,
    )
    restart_guard = _reconcile_restart_guard_until_stable(
        db_path=options.db_path,
        store=session_guard_store,
        profile_scope_key=profile_identity.profile_scope_key,
        reconciled_at=clock(),
    )
    session_guard_runtime = FacebookAutomationSessionGuardRuntime(session_guard_store)
    access_runtime_gate = FacebookAccessRuntimeGate()
    admission_controller = FacebookAutomationAdmissionController(
        db_path=options.db_path,
        profile_scope_key=profile_identity.profile_scope_key,
        runtime_gate=access_runtime_gate,
        coordinator=automation_coordinator,
        sleep_fn=automation_sleep,
        pacing_lease_seconds=(
            PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.persistent_lease_seconds
        ),
        persistent_quiet_gap_seconds=(
            PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.persistent_quiet_gap_seconds
        ),
        clock=clock,
        session_guard_runtime=session_guard_runtime,
    )
    return (
        ResidentSupervisorRuntime(
            options=options,
            scan_page=scan_page,
            comments_commit_ready_scan_page=comments_commit_ready_scan_page,
            schedule_planner=schedule_planner,
            stop_requested=stop_requested,
            sleep=sleep,
            clock=clock,
            on_cycle=on_cycle,
            summaries=summaries,
            profile_scope_key=profile_identity.profile_scope_key,
            session_guard_store=session_guard_store,
            session_guard_runtime=session_guard_runtime,
            automation_coordinator=automation_coordinator,
            admission_controller=admission_controller,
            access_runtime_gate=access_runtime_gate,
        ),
        restart_guard,
    )


def _resident_supervisor_should_run(
    runtime: ResidentSupervisorRuntime,
    cycle_index: int,
) -> bool:
    """依原始 stop/max-cycle 邊界決定是否開始下一輪。"""

    return not runtime.stop_requested() and (
        runtime.options.max_cycles is None or cycle_index < runtime.options.max_cycles
    )


async def _run_resident_supervisor_iteration(
    runtime: ResidentSupervisorRuntime,
    transition: ResidentSupervisorTransition,
) -> ResidentSupervisorTransition:
    """執行一輪 typed safety/recovery/browser supervisor 轉移。"""

    state = _load_resident_supervisor_state(runtime, transition.restart_guard)
    action = _select_resident_supervisor_action(state)
    if action == ResidentSupervisorAction.SESSION_RECOVERY_PROBE:
        return await _run_session_recovery_supervisor_action(
            runtime,
            state,
            transition.cycle_index,
        )
    if action == ResidentSupervisorAction.MANUAL_ACCESS_PROBE:
        return await _run_manual_probe_supervisor_action(
            runtime,
            state,
            transition.cycle_index,
        )
    if action == ResidentSupervisorAction.DORMANT:
        return await _advance_dormant_supervisor_cycle(
            runtime,
            cycle_index=transition.cycle_index,
            restart_guard=state.restart_guard,
        )
    return await _run_browser_session_supervisor_action(
        runtime,
        cycle_index=transition.cycle_index,
    )


def _load_resident_supervisor_state(
    runtime: ResidentSupervisorRuntime,
    restart_guard: FacebookAutomationRestartGuardResult,
) -> ResidentSupervisorState:
    """先回收過期 owner，再依原順序讀取單輪 durable safety state。"""

    recovered_at = runtime.clock()
    _recover_expired_facebook_half_open(
        runtime.options.db_path,
        runtime.profile_scope_key,
        recovered_at=recovered_at,
    )
    _recover_expired_facebook_session_probe(
        runtime.options.db_path,
        runtime.profile_scope_key,
        recovered_at=runtime.clock(),
    )
    circuit_state = _facebook_circuit_state(
        runtime.options.db_path,
        runtime.profile_scope_key,
    )
    circuit_status = (
        circuit_state.status
        if circuit_state is not None
        else FacebookAccessCircuitStatus.CLOSED
    )
    return ResidentSupervisorState(
        restart_guard=restart_guard,
        circuit_state=circuit_state,
        circuit_status=circuit_status,
        runtime_gate_closed=runtime.access_runtime_gate.snapshot().writes_closed,
        session_recovery_state=_facebook_session_recovery_state(
            runtime.options.db_path,
            runtime.profile_scope_key,
        ),
    )


def _select_resident_supervisor_action(
    state: ResidentSupervisorState,
) -> ResidentSupervisorAction:
    """將 durable state 純函式映射為本輪唯一 supervisor action。"""

    recovery = state.session_recovery_state
    if (
        not state.restart_guard.browser_io_allowed
        and state.restart_guard.outcome
        == FacebookAutomationRestartGuardOutcome.UNCLEAN_SESSION_HOLD
        and state.circuit_status == FacebookAccessCircuitStatus.CLOSED
        and recovery is not None
        and recovery.status == FacebookSessionRecoveryStatus.PROBE_PENDING
    ):
        return ResidentSupervisorAction.SESSION_RECOVERY_PROBE
    if (
        state.restart_guard.browser_io_allowed
        and state.circuit_status == FacebookAccessCircuitStatus.OPEN
        and state.circuit_state is not None
        and state.circuit_state.probe_request_id
    ):
        return ResidentSupervisorAction.MANUAL_ACCESS_PROBE
    if (
        not state.restart_guard.browser_io_allowed
        or state.circuit_status != FacebookAccessCircuitStatus.CLOSED
        or state.runtime_gate_closed
    ):
        return ResidentSupervisorAction.DORMANT
    return ResidentSupervisorAction.BROWSER_SESSION


async def _run_session_recovery_supervisor_action(
    runtime: ResidentSupervisorRuntime,
    state: ResidentSupervisorState,
    cycle_index: int,
) -> ResidentSupervisorTransition:
    """消化 stale-session probe，context 收旂後立即重讀 marker。"""

    result = await consume_pending_facebook_session_recovery_probe(
        options=runtime.options,
        profile_scope_key=runtime.profile_scope_key,
        coordinator=runtime.automation_coordinator,
        admission_controller=runtime.admission_controller,
        runtime_gate=runtime.access_runtime_gate,
        session_guard_store=runtime.session_guard_store,
        should_stop=runtime.stop_requested,
        clock=runtime.clock,
    )
    restart_guard = state.restart_guard
    if result.context_closed:
        restart_guard = _reconcile_restart_guard_until_stable(
            db_path=runtime.options.db_path,
            store=runtime.session_guard_store,
            profile_scope_key=runtime.profile_scope_key,
            reconciled_at=runtime.clock(),
        )
    if not result.cleanup_completed:
        logger.error(
            "resident_recovery_probe_cleanup_incomplete "
            "probe=session_recovery action=stop_runtime"
        )
    return await _advance_dormant_supervisor_cycle(
        runtime,
        cycle_index=cycle_index,
        restart_guard=restart_guard,
        stop_after_cycle=not result.cleanup_completed,
    )


async def _run_manual_probe_supervisor_action(
    runtime: ResidentSupervisorRuntime,
    state: ResidentSupervisorState,
    cycle_index: int,
) -> ResidentSupervisorTransition:
    """消化 manual access probe，僅在 verified closed 後重置 process gate。"""

    result = await consume_pending_facebook_manual_probe(
        options=runtime.options,
        profile_scope_key=runtime.profile_scope_key,
        coordinator=runtime.automation_coordinator,
        admission_controller=runtime.admission_controller,
        session_guard_runtime=runtime.session_guard_runtime,
        should_stop=runtime.stop_requested,
        clock=runtime.clock,
    )
    restart_guard = state.restart_guard
    if runtime.session_guard_store.read() is not None:
        restart_guard = _reconcile_restart_guard_until_stable(
            db_path=runtime.options.db_path,
            store=runtime.session_guard_store,
            profile_scope_key=runtime.profile_scope_key,
            reconciled_at=runtime.clock(),
        )
    if result.outcome == FacebookManualProbeOutcome.SUCCEEDED:
        verified_state = _facebook_circuit_state(
            runtime.options.db_path,
            runtime.profile_scope_key,
        )
        if (
            verified_state is not None
            and verified_state.status == FacebookAccessCircuitStatus.CLOSED
        ):
            runtime.access_runtime_gate.reset_after_verified_closed_circuit()
    if not result.cleanup_completed:
        logger.error(
            "resident_recovery_probe_cleanup_incomplete "
            "probe=manual action=stop_runtime"
        )
    return await _advance_dormant_supervisor_cycle(
        runtime,
        cycle_index=cycle_index,
        restart_guard=restart_guard,
        stop_after_cycle=not result.cleanup_completed,
    )


async def _advance_dormant_supervisor_cycle(
    runtime: ResidentSupervisorRuntime,
    *,
    cycle_index: int,
    restart_guard: FacebookAutomationRestartGuardResult,
    stop_after_cycle: bool = False,
) -> ResidentSupervisorTransition:
    """保留 dormant cycle 語義；fatal cleanup 後立即停止本 runtime。"""

    next_cycle_index = cycle_index + 1
    summary = _build_circuit_dormant_summary(next_cycle_index)
    runtime.summaries.append(summary)
    if runtime.on_cycle:
        runtime.on_cycle(summary)
    reached_max_cycles = (
        runtime.options.max_cycles is not None
        and next_cycle_index >= runtime.options.max_cycles
    )
    if reached_max_cycles or stop_after_cycle:
        disposition = ResidentSupervisorDisposition.BREAK
    else:
        await runtime.sleep(max(runtime.options.scheduler_tick_seconds, 1.0))
        disposition = ResidentSupervisorDisposition.CONTINUE
    return ResidentSupervisorTransition(
        cycle_index=next_cycle_index,
        restart_guard=restart_guard,
        disposition=disposition,
    )


async def _run_browser_session_supervisor_action(
    runtime: ResidentSupervisorRuntime,
    *,
    cycle_index: int,
) -> ResidentSupervisorTransition:
    """執行單一 browser runtime session，並依 circuit/restart 結果轉移。"""

    session_result = await _run_resident_browser_runtime_session(
        options=runtime.options,
        scan_page=runtime.scan_page,
        comments_commit_ready_scan_page=runtime.comments_commit_ready_scan_page,
        schedule_planner=runtime.schedule_planner,
        stop_requested=runtime.stop_requested,
        sleep=runtime.sleep,
        on_cycle=runtime.on_cycle,
        summaries=runtime.summaries,
        cycle_index=cycle_index,
        automation_coordinator=runtime.automation_coordinator,
        automation_admission_controller=runtime.admission_controller,
    )
    restart_guard = _reconcile_restart_guard_until_stable(
        db_path=runtime.options.db_path,
        store=runtime.session_guard_store,
        profile_scope_key=runtime.profile_scope_key,
        reconciled_at=runtime.clock(),
    )
    circuit = _facebook_circuit_state(
        runtime.options.db_path,
        runtime.profile_scope_key,
    )
    circuit_requires_next_tick = (
        circuit is not None
        and circuit.status != FacebookAccessCircuitStatus.CLOSED
        and not runtime.stop_requested()
    )
    disposition = (
        ResidentSupervisorDisposition.CONTINUE
        if circuit_requires_next_tick or session_result.runtime_restart_requested
        else ResidentSupervisorDisposition.BREAK
    )
    return ResidentSupervisorTransition(
        cycle_index=session_result.cycle_index,
        restart_guard=restart_guard,
        disposition=disposition,
    )


def _facebook_circuit_state(
    db_path: Path,
    profile_scope_key: str,
) -> FacebookAccessCircuitSnapshot | None:
    """在建立 Playwright runtime 前讀取 persistent circuit；未知狀態 fail closed。"""

    with SqliteApplicationContext(db_path) as app:
        return app.services.facebook_access_circuit.get(profile_scope_key)


def _reconcile_restart_guard_until_stable(
    *,
    db_path: Path,
    store: FacebookAutomationSessionGuardStore,
    profile_scope_key: str,
    reconciled_at: datetime,
) -> FacebookAutomationRestartGuardResult:
    """收旂restart marker；trip durable後再讀一次避免同process卡住。"""

    result = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=store,
        profile_scope_key=profile_scope_key,
        reconciled_at=reconciled_at,
    )
    if result.safety_hold is not None:
        result = reconcile_facebook_automation_restart_guard(
            db_path=db_path,
            store=store,
            profile_scope_key=profile_scope_key,
            reconciled_at=reconciled_at,
        )
    return result


def _facebook_session_recovery_state(
    db_path: Path,
    profile_scope_key: str,
) -> FacebookSessionRecoverySnapshot | None:
    """讀取 stale normal-session recovery truth，不建立 browser。"""

    with SqliteApplicationContext(db_path) as app:
        return app.services.facebook_session_recovery.get(profile_scope_key)


def _recover_expired_facebook_half_open(
    db_path: Path,
    profile_scope_key: str,
    *,
    recovered_at: datetime,
) -> None:
    """Dormant supervisor以DB CAS回收過期probe lease，全程不建立browser。"""

    with SqliteApplicationContext(db_path) as app:
        app.services.facebook_access_circuit.recover_expired_half_open(
            profile_scope_key,
            recovered_at=recovered_at,
        )


def _recover_expired_facebook_session_probe(
    db_path: Path,
    profile_scope_key: str,
    *,
    recovered_at: datetime,
) -> None:
    """在 browser I/O 前回收 stale-session 過期 probe owner。"""

    with SqliteApplicationContext(db_path) as app:
        app.services.facebook_session_recovery.recover_expired_probe(
            profile_scope_key,
            recovered_at=recovered_at,
        )


def _build_circuit_dormant_summary(cycle_index: int) -> ResidentCycleSummary:
    """建立 browser-free circuit dormant tick 摘要。"""

    return ResidentCycleSummary(
        cycle_index=cycle_index,
        selected_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
        opened_page_count=0,
        reused_page_count=0,
        closed_page_count=0,
        resident_browser_alive=False,
        worker_health_ok=True,
    )


def _facebook_automation_session_guard_store(
    options: ResidentRuntimeOptions,
    *,
    profile_scope_key: str,
) -> FacebookAutomationSessionGuardStore:
    """依managed profile正式layout取得runtime-path-owned sentinel store。"""

    profile_parent = options.profile_dir.expanduser().resolve().parent
    data_dir = (
        profile_parent.parent
        if profile_parent.name.casefold() == "profiles"
        else options.db_path.expanduser().resolve().parent
    )
    return FacebookAutomationSessionGuardStore(
        data_dir / FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME,
        profile_alias=derive_facebook_automation_profile_alias(profile_scope_key),
    )


async def _run_resident_browser_runtime_session(
    *,
    options: ResidentRuntimeOptions,
    scan_page: AsyncCommitReadyScanCallable,
    comments_commit_ready_scan_page: AsyncCommitReadyScanCallable | None,
    schedule_planner: TargetSchedulePlanner,
    stop_requested: StopCheckCallable,
    sleep: AsyncSleepCallable,
    on_cycle: AsyncCycleObserver | None,
    summaries: list[ResidentCycleSummary],
    cycle_index: int,
    automation_coordinator: FacebookAutomationCoordinator,
    automation_admission_controller: FacebookAutomationAdmissionController,
) -> ResidentRuntimeSessionResult:
    """執行單一 Playwright persistent context runtime session。"""

    target_queue = TargetQueue()
    with acquire_profile_lease(options.profile_dir, "resident main worker"):
        await automation_admission_controller.recover_expired_pacing_lease()
        pacing_available = await _wait_for_startup_pacing_or_stop(
            automation_admission_controller=automation_admission_controller,
            automation_coordinator=automation_coordinator,
            stop_requested=stop_requested,
        )
        if not pacing_available:
            return ResidentRuntimeSessionResult(
                cycle_index=cycle_index,
                runtime_restart_requested=False,
            )
        automation_admission_controller.rotate_owner_session_id()
        automation_admission_controller.start_session_guard_before_browser_io()
        browser_context_closed = False
        try:
            async with async_playwright() as playwright:
                browser_context = await launch_persistent_context_async(
                    playwright,
                    BrowserRuntimeOptions(
                        profile_dir=options.profile_dir,
                        headless=not options.headed_compat,
                        timeout_seconds=_browser_runtime_timeout_seconds(options),
                    ),
                )
                try:
                    _set_browser_context_timeouts(browser_context, options)
                    await close_existing_context_pages_async(browser_context)
                    page_pool = AsyncResidentPagePool(
                        browser_context,
                        max_open_pages=(
                            PYTHON_FACEBOOK_AUTOMATION_DEFAULTS.max_open_facebook_pages
                        ),
                        retain_idle_pages=False,
                    )
                    executor = ExecutorWorkerPool(
                        options=options,
                        page_pool=page_pool,
                        target_queue=target_queue,
                        schedule_planner=schedule_planner,
                        scan_page=scan_page,
                        automation_coordinator=automation_coordinator,
                        automation_admission_controller=automation_admission_controller,
                        **(
                            {
                                "comments_commit_ready_scan_page": (
                                    comments_commit_ready_scan_page
                                )
                            }
                            if comments_commit_ready_scan_page is not None
                            else {}
                        ),
                    )
                    await executor.start()
                    stop_monitor_task = asyncio.create_task(
                        _cancel_automation_waiters_when_stopping(
                            stop_requested=stop_requested,
                            automation_coordinator=automation_coordinator,
                        ),
                        name="facebook-automation-stop-monitor",
                    )
                    try:
                        try:
                            return await _run_scheduler_ticks_until_restart(
                                options=options,
                                browser_context=browser_context,
                                page_pool=page_pool,
                                target_queue=target_queue,
                                executor=executor,
                                schedule_planner=schedule_planner,
                                stop_requested=stop_requested,
                                sleep=sleep,
                                on_cycle=on_cycle,
                                summaries=summaries,
                                cycle_index=cycle_index,
                                automation_coordinator=automation_coordinator,
                                automation_admission_controller=automation_admission_controller,
                            )
                        finally:
                            runtime_restart_requested = executor.runtime_restart_requested()
                            await executor.stop(
                                cancel_running=(stop_requested() or runtime_restart_requested),
                                runtime_restart=runtime_restart_requested,
                            )
                            await page_pool.close_all()
                    finally:
                        stop_monitor_task.cancel()
                        await asyncio.gather(stop_monitor_task, return_exceptions=True)
                finally:
                    await browser_context.close()
                    browser_context_closed = True
        finally:
            if browser_context_closed:
                automation_admission_controller.finish_session_guard_after_browser_context_closed()


async def _run_scheduler_ticks_until_restart(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike,
    page_pool: AsyncResidentPagePool,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
    schedule_planner: TargetSchedulePlanner,
    stop_requested: StopCheckCallable,
    sleep: AsyncSleepCallable,
    on_cycle: AsyncCycleObserver | None,
    summaries: list[ResidentCycleSummary],
    cycle_index: int,
    automation_coordinator: FacebookAutomationCoordinator,
    automation_admission_controller: FacebookAutomationAdmissionController,
) -> ResidentRuntimeSessionResult:
    """在單一 runtime session 中執行 scheduler ticks 直到停止或 restart。"""

    runtime_restart_requested = False
    while not stop_requested() and (options.max_cycles is None or cycle_index < options.max_cycles):
        if executor.runtime_restart_requested():
            runtime_restart_requested = True
            break
        cycle_index += 1
        summary = await run_resident_main_scheduler_tick(
            options=options,
            browser_context=browser_context,
            page_pool=page_pool,
            target_queue=target_queue,
            executor=executor,
            schedule_planner=schedule_planner,
            cycle_index=cycle_index,
            automation_coordinator=automation_coordinator,
            automation_admission_controller=automation_admission_controller,
            should_stop=stop_requested,
        )
        summaries.append(summary)
        if on_cycle:
            on_cycle(summary)
        if executor.runtime_restart_requested():
            runtime_restart_requested = True
            break
        if not summary.worker_health_ok:
            _request_restart_for_unhealthy_workers(executor, summary)
            runtime_restart_requested = True
            break
        if options.max_cycles is not None and cycle_index >= options.max_cycles:
            break
        if await _sleep_or_runtime_restart(
            sleep_fn=sleep,
            seconds=max(options.scheduler_tick_seconds, 0),
            executor=executor,
        ):
            runtime_restart_requested = True
            break
    if not stop_requested() and not runtime_restart_requested:
        runtime_restart_requested = await _drain_queue_or_runtime_restart(
            target_queue=target_queue,
            executor=executor,
        )
    return ResidentRuntimeSessionResult(
        cycle_index=cycle_index,
        runtime_restart_requested=runtime_restart_requested,
    )


async def _cancel_automation_waiters_when_stopping(
    *,
    stop_requested: StopCheckCallable,
    automation_coordinator: FacebookAutomationCoordinator,
) -> None:
    """監看同步 stop callback，並取消 coordinator 尚未取得 ownership 的 waiters。"""

    while not stop_requested():
        await asyncio.sleep(0.05)
    automation_coordinator.cancel_pending_waiters()


async def _wait_for_startup_pacing_or_stop(
    *,
    automation_admission_controller: FacebookAutomationAdmissionController,
    automation_coordinator: FacebookAutomationCoordinator,
    stop_requested: StopCheckCallable,
) -> bool:
    """等待 startup pacing；stop 先到時取消等待且不建立 browser context。"""

    pacing_task = asyncio.create_task(
        automation_admission_controller.wait_until_pacing_available(),
        name="facebook-automation-startup-pacing",
    )
    try:
        while not pacing_task.done():
            if stop_requested():
                automation_coordinator.cancel_pending_waiters()
                pacing_task.cancel()
                await asyncio.gather(pacing_task, return_exceptions=True)
                return False
            await asyncio.wait({pacing_task}, timeout=0.05)
        await pacing_task
        return True
    finally:
        if not pacing_task.done():
            pacing_task.cancel()
            await asyncio.gather(pacing_task, return_exceptions=True)


def _browser_runtime_timeout_seconds(options: ResidentRuntimeOptions) -> float:
    """回傳 browser runtime timeout，與 scan timeout 下限保持一致。"""

    return max(
        options.scan_timeout_seconds,
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
    )


def _set_browser_context_timeouts(
    browser_context: BrowserTimeoutContextLike,
    options: ResidentRuntimeOptions,
) -> None:
    """設定 Playwright context 的 default timeout 與 navigation timeout。"""

    timeout_ms = _browser_runtime_timeout_seconds(options) * 1000
    browser_context.set_default_timeout(timeout_ms)
    browser_context.set_default_navigation_timeout(timeout_ms)


def _request_restart_for_unhealthy_workers(
    executor: ExecutorWorkerPool,
    summary: ResidentCycleSummary,
) -> None:
    """worker pool unhealthy 時記錄原因並要求 runtime restart。"""

    logger.warning(
        "resident_main_runtime_restart_requested reason=%s cycle=%s worker_statuses=%s",
        "worker_pool_unhealthy",
        summary.cycle_index,
        ",".join(summary.worker_statuses),
    )
    executor.request_runtime_restart()


def _publish_display_next_due_at(
    db_path: Path,
) -> Callable[[str, datetime | None], None]:
    """建立 scheduler due time 發布器；DB 欄位只供 dashboard 顯示。"""

    def publish(target_id: str, due_at: datetime | None) -> None:
        """將 planner 已決定的 next due 寫入 read model。"""

        future = _DISPLAY_NEXT_DUE_EXECUTOR.submit(
            _write_display_next_due_at_best_effort,
            db_path,
            target_id,
            due_at,
        )
        future.add_done_callback(
            lambda done: _log_display_next_due_update_exception(done, target_id)
        )

    return publish


def _write_display_next_due_at_best_effort(
    db_path: Path,
    target_id: str,
    due_at: datetime | None,
) -> None:
    """以短 timeout 更新 UI-only next due read model；lock 時直接略過。"""

    try:
        with closing(sqlite3.connect(db_path, timeout=0.1)) as connection:
            connection.execute(f"PRAGMA busy_timeout = {_DISPLAY_NEXT_DUE_BUSY_TIMEOUT_MS}")
            connection.execute(
                """
                UPDATE target_runtime_state
                SET display_next_due_at = ?, updated_at = ?
                WHERE target_id = ?
                """,
                (
                    encode_datetime(due_at),
                    encode_datetime(utc_now()),
                    target_id,
                ),
            )
            connection.commit()
    except sqlite3.OperationalError as exc:
        if not is_sqlite_lock_error(exc):
            raise
        logger.warning(
            "display next due update skipped: database locked target_id=%s exception_class=%s",
            target_id,
            exc.__class__.__name__,
        )


def _log_display_next_due_update_exception(
    future: Future[None],
    target_id: str,
) -> None:
    """記錄背景 display-next-due 更新的非 lock 例外。"""

    exc = future.exception()
    if exc is None:
        return
    if is_sqlite_lock_error(exc):
        logger.warning(
            "display next due update skipped: database locked target_id=%s exception_class=%s",
            target_id,
            exc.__class__.__name__,
        )
        return
    logger.error(
        "display next due update failed target_id=%s exception_class=%s",
        target_id,
        exc.__class__.__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


async def run_resident_main_scheduler_tick(
    *,
    options: ResidentRuntimeOptions,
    browser_context: GroupMetadataBrowserContextLike | None = None,
    page_pool: AsyncResidentPagePool,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
    schedule_planner: TargetSchedulePlanner,
    cycle_index: int,
    automation_coordinator: FacebookAutomationCoordinator | None = None,
    automation_admission_controller: FacebookAutomationAdmissionController | None = None,
    should_stop: StopCheckCallable | None = None,
) -> ResidentCycleSummary:
    """producer-only scheduler tick：只負責發現 due targets 並 enqueue。"""

    stop_requested = should_stop or (lambda: False)
    recovery_summary = recover_stale_runtime_targets_detailed(
        options.db_path,
        options.stale_running_after_seconds,
    )
    recovery_result = await ResidentRecoveryCoordinator(
        executor=executor,
        page_pool=page_pool,
        target_queue=target_queue,
    ).apply(recovery_summary.running_actions)
    notification_dispatch_count = dispatch_pending_notification_outbox(options)
    run_bounded_retention_maintenance_if_due(options)
    active_target_ids = list_active_resident_target_ids(options.db_path)
    closed_page_count = await page_pool.close_inactive(active_target_ids)
    enqueued_count = 0
    due_targets: tuple[DueTarget, ...] = ()
    if not stop_requested() and not executor.runtime_restart_requested():
        due_targets = schedule_planner.list_due_targets(
            options.db_path,
            default_interval_seconds=options.interval_seconds,
            max_count=1,
        )
        enqueued_count = await executor.enqueue_due_targets(due_targets)
    metadata_refresh_count = 0
    cover_image_refresh_count = 0
    if not due_targets and not stop_requested() and not executor.runtime_restart_requested():
        metadata_refresh_count = await resident_metadata_refresh.refresh_requested_target_metadata(
            options=options,
            browser_context=browser_context,
            should_stop=stop_requested,
            request_runtime_restart=executor.request_runtime_restart,
            automation_coordinator=automation_coordinator,
            automation_admission_controller=automation_admission_controller,
        )
    if not due_targets and not stop_requested() and not executor.runtime_restart_requested():
        cover_image_refresh_count = (
            await resident_cover_image_refresh.refresh_pending_target_cover_images(
                options=options,
                browser_context=browser_context,
                should_stop=stop_requested,
                request_runtime_restart=executor.request_runtime_restart,
                automation_coordinator=automation_coordinator,
                automation_admission_controller=automation_admission_controller,
            )
        )
    counters = await executor.take_counters()
    worker_health_ok = executor.worker_health_ok()
    runtime_restart_requested = executor.runtime_restart_requested()
    queued_count, running_count, queued_ids = await target_queue.snapshot()
    coordinator_snapshot = (
        await automation_coordinator.snapshot()
        if automation_coordinator is not None
        else None
    )
    summary = ResidentCycleSummary(
        cycle_index=cycle_index,
        selected_count=enqueued_count,
        success_count=counters.success_count,
        failure_count=counters.failure_count,
        skipped_count=counters.skipped_count,
        opened_page_count=counters.opened_page_count,
        reused_page_count=counters.reused_page_count,
        closed_page_count=closed_page_count
        + metadata_refresh_count
        + cover_image_refresh_count
        + recovery_result.discarded_page_count,
        queued_count=queued_count,
        running_count=running_count,
        queue_length=queued_count,
        queued_target_ids=queued_ids,
        worker_ids=executor.worker_ids,
        worker_statuses=executor.worker_statuses(),
        page_pool_size=await page_pool.size(),
        resident_browser_alive=worker_health_ok and not runtime_restart_requested,
        recovered_runtime_count=recovery_summary.recovered_count,
        metadata_refresh_count=metadata_refresh_count,
        cover_image_refresh_count=cover_image_refresh_count,
        notification_dispatch_count=notification_dispatch_count,
        worker_health_ok=worker_health_ok,
        automation_coordinator_active=bool(
            coordinator_snapshot is not None and coordinator_snapshot.active
        ),
        automation_coordinator_work_kind=(
            coordinator_snapshot.active_work_kind
            if coordinator_snapshot is not None
            else ""
        ),
        automation_coordinator_waiter_count=(
            coordinator_snapshot.waiter_count
            if coordinator_snapshot is not None
            else 0
        ),
    )
    _log_resident_scheduler_tick_summary(summary, options=options)
    return summary


def _log_resident_scheduler_tick_summary(
    summary: ResidentCycleSummary,
    *,
    options: ResidentRuntimeOptions,
) -> None:
    """記錄 resident scheduler tick 的 queue/worker 診斷摘要。"""

    if not _should_log_resident_scheduler_tick_summary(summary):
        return
    logger.info(
        "resident_scheduler_tick cycle=%s selected=%s success=%s failure=%s "
        "skipped=%s running=%s queued=%s queue_length=%s queued_target_ids=%s "
        "max_concurrent_scans=%s worker_ids=%s worker_statuses=%s "
        "opened_pages=%s reused_pages=%s "
        "closed_pages=%s page_pool_size=%s recovered_runtime=%s metadata_refresh=%s "
        "cover_image_refresh=%s notification_dispatch=%s browser_alive=%s "
        "worker_health_ok=%s",
        summary.cycle_index,
        summary.selected_count,
        summary.success_count,
        summary.failure_count,
        summary.skipped_count,
        summary.running_count,
        summary.queued_count,
        summary.queue_length,
        ",".join(summary.queued_target_ids),
        options.max_concurrent_scans,
        ",".join(summary.worker_ids),
        ",".join(summary.worker_statuses),
        summary.opened_page_count,
        summary.reused_page_count,
        summary.closed_page_count,
        summary.page_pool_size,
        summary.recovered_runtime_count,
        summary.metadata_refresh_count,
        summary.cover_image_refresh_count,
        summary.notification_dispatch_count,
        summary.resident_browser_alive,
        summary.worker_health_ok,
    )


def _should_log_resident_scheduler_tick_summary(summary: ResidentCycleSummary) -> bool:
    """只在本輪有排程活動或可診斷狀態時輸出 INFO log。"""

    return any(
        (
            summary.selected_count,
            summary.success_count,
            summary.failure_count,
            summary.skipped_count,
            summary.running_count,
            summary.queued_count,
            summary.recovered_runtime_count,
            summary.metadata_refresh_count,
            summary.cover_image_refresh_count,
            summary.notification_dispatch_count,
            not summary.worker_health_ok,
            not summary.resident_browser_alive,
        )
    )


def dispatch_pending_notification_outbox(options: ResidentRuntimeOptions) -> int:
    """喚醒 outbox dispatcher；standalone fallback 才同步 drain pending rows。"""

    try:
        if wake_notification_outbox_dispatcher_for_db(options.db_path):
            return 0
        return dispatch_new_pending_notification_outbox_for_db(
            db_path=options.db_path,
        ).dispatched_count
    except sqlite3.OperationalError as exc:
        if _is_sqlite_database_locked(exc):
            logger.warning("pending notification outbox dispatch skipped: database locked")
            return 0
        logger.exception("pending notification outbox dispatch failed")
        return 0


def run_bounded_retention_maintenance_if_due(options: ResidentRuntimeOptions) -> int:
    """週期性清理 bounded retention horizon 外的內部資料。"""

    return run_bounded_retention_maintenance_for_db(options.db_path)


def _is_sqlite_database_locked(exc: sqlite3.OperationalError) -> bool:
    """判斷 SQLite OperationalError 是否為暫時性 lock contention。"""

    return is_sqlite_lock_error(exc)


async def _sleep_or_runtime_restart(
    *,
    sleep_fn: AsyncSleepCallable,
    seconds: float,
    executor: ExecutorWorkerPool,
) -> bool:
    """等待下一輪排程或 runtime restart request，先到者為準。"""

    if executor.runtime_restart_requested():
        return True
    sleep_task = asyncio.create_task(sleep_fn(max(seconds, 0)))
    restart_task = asyncio.create_task(executor.wait_runtime_restart_requested())
    try:
        done, pending = await asyncio.wait(
            {sleep_task, restart_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if sleep_task in done:
            await sleep_task
        if restart_task in done:
            await restart_task
        return restart_task in done or executor.runtime_restart_requested()
    finally:
        for task in (sleep_task, restart_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(sleep_task, restart_task, return_exceptions=True)


async def _drain_queue_or_runtime_restart(
    *,
    target_queue: TargetQueue,
    executor: ExecutorWorkerPool,
) -> bool:
    """等待 queue drain；若 runtime restart 先發生則交給外層重建。"""

    if executor.runtime_restart_requested():
        return True
    join_task = asyncio.create_task(target_queue.join())
    restart_task = asyncio.create_task(executor.wait_runtime_restart_requested())
    try:
        done, _pending = await asyncio.wait(
            {join_task, restart_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if join_task in done:
            await join_task
        if restart_task in done:
            await restart_task
            return True
        return executor.runtime_restart_requested()
    finally:
        for task in (join_task, restart_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(join_task, restart_task, return_exceptions=True)


def run_resident_main_loop_sync(
    options: ResidentRuntimeOptions,
    *,
    should_stop: StopCheckCallable | None = None,
    on_cycle: AsyncCycleObserver | None = None,
    sleep_fn: Callable[[float], object] | None = None,
) -> list[ResidentCycleSummary]:
    """同步包裝 resident main worker，供 CLI / Web UI background thread 呼叫。"""

    async def run_with_shutdown_handler() -> list[ResidentCycleSummary]:
        """讓 Playwright shutdown handler 維持到 asyncio.run 關閉 event loop。"""

        _install_playwright_shutdown_exception_handler()
        return await run_resident_main_loop(
            options,
            should_stop=should_stop,
            on_cycle=on_cycle,
            sleep_fn=selected_sleep,
        )

    async def selected_sleep(seconds: float) -> None:
        """橋接既有同步 wake-aware sleep_fn 到 async worker。"""

        if sleep_fn is None:
            await asyncio.sleep(seconds)
            return
        await asyncio.to_thread(sleep_fn, seconds)

    return asyncio.run(run_with_shutdown_handler())
