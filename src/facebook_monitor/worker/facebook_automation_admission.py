"""Circuit + process coordinator 的 Facebook normal-work admission。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from contextlib import AbstractContextManager
from collections.abc import Awaitable
from collections.abc import Callable
import asyncio
from uuid import UUID
from uuid import uuid4

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAdmissionOutcome
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_automation_pacing import FacebookAutomationPacingToken
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireResult
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingFinishResult
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingRecoveryOutcome
from facebook_monitor.core.models import utc_now
from facebook_monitor.worker.facebook_access_runtime_gate import (
    FacebookAccessRuntimeGate,
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
    FacebookAutomationSessionGuardRuntime,
)


class FacebookGovernedAutomationLease:
    """保存 process work lease 與 persistent circuit admission token。"""

    def __init__(
        self,
        *,
        process_lease: FacebookAutomationLease,
        admission_token: FacebookAdmissionToken,
        operation_kind: FacebookProductOperationKind,
        pacing_token: FacebookAutomationPacingToken,
        db_path: Path,
        persistent_quiet_gap_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self.process_lease = process_lease
        self.admission_token = admission_token
        self.operation_kind = operation_kind
        self.pacing_token = pacing_token
        self.db_path = db_path
        self.persistent_quiet_gap_seconds = max(
            float(persistent_quiet_gap_seconds),
            0.0,
        )
        self.clock = clock
        self._released = False
        self._pacing_finished = False

    async def release(self) -> None:
        """冪等釋放 pacing/process owner，並保留可重試狀態。"""

        if self._released:
            return
        release_error: BaseException | None = None
        try:
            if not self._pacing_finished:
                finished_at = self.clock()
                with SqliteApplicationContext(self.db_path) as app:
                    app.repositories.facebook_automation_pacing.finish(
                        self.pacing_token,
                        finished_at=finished_at,
                        next_not_before=finished_at
                        + timedelta(seconds=self.persistent_quiet_gap_seconds),
                        outcome="finished",
                    )
                self._pacing_finished = True
        except BaseException as exc:
            release_error = exc
        try:
            await self.process_lease.release()
        except BaseException as exc:
            if release_error is None:
                release_error = exc
        self._released = self._pacing_finished and self.process_lease.released
        if release_error is not None:
            raise release_error


@dataclass(frozen=True)
class FacebookAutomationAdmissionResult:
    """Normal Facebook work admission 的 typed 結果。"""

    lease: FacebookGovernedAutomationLease | None
    circuit_state: FacebookAccessCircuitSnapshot
    reason: str = ""

    @property
    def admitted(self) -> bool:
        """回傳是否可開始任何 browser/page work。"""

        return self.lease is not None


class FacebookAutomationAdmissionController:
    """依 breaker→FIFO/quiet-gap→breaker recheck 順序核發 work lease。"""

    def __init__(
        self,
        *,
        db_path: Path,
        profile_scope_key: str,
        runtime_gate: FacebookAccessRuntimeGate,
        coordinator: FacebookAutomationCoordinator,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = utc_now,
        pacing_lease_seconds: float = 5 * 60,
        persistent_quiet_gap_seconds: float = 30,
        owner_session_id: str | None = None,
        session_guard_runtime: FacebookAutomationSessionGuardRuntime | None = None,
    ) -> None:
        self.db_path = db_path
        self.profile_scope_key = profile_scope_key
        self.runtime_gate = runtime_gate
        self.coordinator = coordinator
        self.sleep_fn = sleep_fn
        self.clock = clock
        self.pacing_lease_seconds = max(float(pacing_lease_seconds), 1.0)
        self.persistent_quiet_gap_seconds = max(
            float(persistent_quiet_gap_seconds),
            0.0,
        )
        self.owner_session_id = _canonical_session_id(owner_session_id or str(uuid4()))
        self.session_guard_runtime = session_guard_runtime

    def rotate_owner_session_id(self) -> str:
        """為下一個browser session建立新的canonical pacing/marker owner。"""

        self.owner_session_id = str(uuid4())
        return self.owner_session_id

    def start_session_guard_before_browser_io(self) -> str:
        """Formal browser入口前以pacing owner建立durable normal marker。"""

        if self.session_guard_runtime is not None:
            marker_session_id = self.session_guard_runtime.start_before_browser_io(
                started_at=self.clock(),
                session_id=self.owner_session_id,
            )
            self.owner_session_id = marker_session_id
        return self.owner_session_id

    def mark_session_guard_trip_pending(
        self,
        *,
        operation_kind: FacebookProductOperationKind,
        trigger_action_kind: FacebookActionKind,
    ) -> None:
        """在trip latch後、incident SQLite transaction前升級durable marker。"""

        if self.session_guard_runtime is not None:
            self.session_guard_runtime.mark_trip_pending(
                operation_kind=operation_kind,
                trigger_action_kind=trigger_action_kind,
            )

    def note_session_guard_incident_committed(self) -> None:
        """標記incident已durable，但不在browser context關閉前移除marker。"""

        if self.session_guard_runtime is not None:
            self.session_guard_runtime.note_incident_committed()

    def finish_session_guard_after_browser_context_closed(self) -> None:
        """Context關閉後才依clean/durable-trip條件移除marker。"""

        if self.session_guard_runtime is not None:
            self.session_guard_runtime.finish_after_browser_context_closed()

    async def recover_expired_pacing_lease(self) -> FacebookPacingRecoveryOutcome:
        """取得 OS profile lease 後，browser I/O 前回收明確過期的 pacing owner。"""

        now = self.clock()
        with SqliteApplicationContext(self.db_path) as app:
            result = app.repositories.facebook_automation_pacing.recover_expired(
                self.profile_scope_key,
                recovered_at=now,
                next_not_before=now + timedelta(seconds=self.persistent_quiet_gap_seconds),
            )
        return result.outcome

    def try_acquire_half_open_probe_pacing(
        self,
        *,
        operation_id: str,
        started_at: datetime | None = None,
        owner_session_id: str | None = None,
    ) -> FacebookPacingAcquireResult:
        """Half-open owner不走normal admit，並可繼承stale marker owner。"""

        now = started_at or self.clock()
        pacing_owner = _canonical_session_id(owner_session_id or self.owner_session_id)
        with SqliteApplicationContext(self.db_path) as app:
            return app.repositories.facebook_automation_pacing.try_acquire(
                self.profile_scope_key,
                operation_id=operation_id,
                work_kind=FacebookAutomationWorkKind.HALF_OPEN_PROBE.value,
                owner_session_id=pacing_owner,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=self.pacing_lease_seconds),
            )

    def finish_half_open_probe_pacing(
        self,
        token: FacebookAutomationPacingToken,
        *,
        outcome: str,
        finished_at: datetime | None = None,
    ) -> FacebookPacingFinishResult:
        """Probe context收斂後釋放persistent owner並套完整quiet gap。"""

        now = finished_at or self.clock()
        with SqliteApplicationContext(self.db_path) as app:
            return app.repositories.facebook_automation_pacing.finish(
                token,
                finished_at=now,
                next_not_before=now
                + timedelta(seconds=self.persistent_quiet_gap_seconds),
                outcome=outcome,
            )

    def normal_visible_write_fence(
        self,
        token: FacebookAdmissionToken,
    ) -> AbstractContextManager[bool]:
        """在 normal SQLite commit 完成前持有 process write fence。"""

        return self.runtime_gate.normal_visible_write_fence(token)

    def db_admission_is_current(
        self,
        app: ApplicationContext,
        token: FacebookAdmissionToken,
    ) -> bool:
        """在 caller 已開始的 write transaction 內重驗 circuit generation。"""

        return app.services.facebook_access_circuit.admission_is_current(
            token,
            process_safety_epoch=token.process_safety_epoch,
        )

    async def wait_until_pacing_available(
        self,
        *,
        process_lease: FacebookAutomationLease | None = None,
    ) -> None:
        """跨 restart 等待 active lease/quiet period，不在 DB transaction 內 sleep。"""

        while True:
            now = self.clock()
            with SqliteApplicationContext(self.db_path) as app:
                state = app.repositories.facebook_automation_pacing.get(self.profile_scope_key)
            if state is None:
                return
            wait_until = None
            if state.active_operation_id:
                wait_until = state.active_lease_expires_at
            elif (
                state.next_automation_not_before is not None
                and now < state.next_automation_not_before
            ):
                wait_until = state.next_automation_not_before
            if wait_until is None:
                return
            delay = max((wait_until - now).total_seconds(), 0.0)
            if delay:
                if process_lease is None:
                    await self.sleep_fn(delay)
                else:
                    await process_lease.wait_or_cancel(self.sleep_fn(delay))
            await self.recover_expired_pacing_lease()

    async def acquire(
        self,
        *,
        work_kind: FacebookAutomationWorkKind,
        operation_kind: FacebookProductOperationKind,
        owner_alias: str = "",
    ) -> FacebookAutomationAdmissionResult:
        """取得 normal-work lease；open/half-open/stale 一律 fail closed。"""

        operation_id = f"facebook-operation-{uuid4()}"
        epoch = self.runtime_gate.current_safety_epoch()
        with SqliteApplicationContext(self.db_path) as app:
            initial = app.services.facebook_access_circuit.admit_normal(
                self.profile_scope_key,
                process_safety_epoch=epoch,
                operation_id=operation_id,
            )
        if initial.outcome != FacebookAdmissionOutcome.ALLOWED or initial.token is None:
            return FacebookAutomationAdmissionResult(
                lease=None,
                circuit_state=initial.state,
                reason="deferred_breaker",
            )

        try:
            process_lease = await self.coordinator.acquire(
                work_kind,
                owner_alias=owner_alias,
            )
        except FacebookAutomationWaitCancelled:
            return FacebookAutomationAdmissionResult(
                lease=None,
                circuit_state=initial.state,
                reason="cancelled_runtime",
            )
        transferred = False
        try:
            if not self.runtime_gate.admission_is_process_current(initial.token):
                return FacebookAutomationAdmissionResult(
                    lease=None,
                    circuit_state=initial.state,
                    reason="deferred_breaker",
                )
            with SqliteApplicationContext(self.db_path) as app:
                current = app.services.facebook_access_circuit.admission_is_current(
                    initial.token,
                    process_safety_epoch=self.runtime_gate.current_safety_epoch(),
                )
                state = app.services.facebook_access_circuit.get(self.profile_scope_key)
            if not current or state is None:
                return FacebookAutomationAdmissionResult(
                    lease=None,
                    circuit_state=state or initial.state,
                    reason="deferred_breaker",
                )
            # 同 process 的 active work 會先持有 coordinator；因此必須在拿到
            # coordinator 後才等待 persistent pacing，否則 enqueue 與 maintenance
            # 會互等到五分鐘 lease timeout。
            try:
                await self.wait_until_pacing_available(process_lease=process_lease)
            except FacebookAutomationWaitCancelled:
                return FacebookAutomationAdmissionResult(
                    lease=None,
                    circuit_state=state,
                    reason="cancelled_runtime",
                )
            if not self.runtime_gate.admission_is_process_current(initial.token):
                return FacebookAutomationAdmissionResult(
                    lease=None,
                    circuit_state=state,
                    reason="deferred_breaker",
                )
            with SqliteApplicationContext(self.db_path) as app:
                current = app.services.facebook_access_circuit.admission_is_current(
                    initial.token,
                    process_safety_epoch=self.runtime_gate.current_safety_epoch(),
                )
                state = app.services.facebook_access_circuit.get(self.profile_scope_key)
            if not current or state is None:
                return FacebookAutomationAdmissionResult(
                    lease=None,
                    circuit_state=state or initial.state,
                    reason="deferred_breaker",
                )
            started_at = self.clock()
            with SqliteApplicationContext(self.db_path) as app:
                pacing = app.repositories.facebook_automation_pacing.try_acquire(
                    self.profile_scope_key,
                    operation_id=operation_id,
                    work_kind=work_kind.value,
                    owner_session_id=self.owner_session_id,
                    started_at=started_at,
                    lease_expires_at=started_at + timedelta(seconds=self.pacing_lease_seconds),
                )
            if pacing.outcome != FacebookPacingAcquireOutcome.ACQUIRED or pacing.token is None:
                return FacebookAutomationAdmissionResult(
                    lease=None,
                    circuit_state=state,
                    reason="deferred_pacing",
                )
            result = FacebookAutomationAdmissionResult(
                lease=FacebookGovernedAutomationLease(
                    process_lease=process_lease,
                    admission_token=initial.token,
                    operation_kind=operation_kind,
                    pacing_token=pacing.token,
                    db_path=self.db_path,
                    persistent_quiet_gap_seconds=(self.persistent_quiet_gap_seconds),
                    clock=self.clock,
                ),
                circuit_state=state,
                reason="facebook_automation_admitted",
            )
            transferred = True
            return result
        finally:
            # 成功時 lease ownership 交給 caller；其餘 branch 必須在 admission 內釋放。
            if not transferred:
                await process_lease.release()


def _canonical_session_id(value: str) -> str:
    """驗證browser session owner為canonical UUID，避免marker/pacing無法對齊。"""

    normalized = str(value).strip().lower()
    parsed = UUID(normalized)
    if str(parsed) != normalized:
        raise ValueError("owner session id must be a canonical UUID")
    return normalized


__all__ = [
    "FacebookAutomationAdmissionController",
    "FacebookAutomationAdmissionResult",
    "FacebookGovernedAutomationLease",
]
