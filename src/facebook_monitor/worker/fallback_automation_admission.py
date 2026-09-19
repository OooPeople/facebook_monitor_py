"""同步 fallback 共用的 managed-profile Facebook automation admission。

職責：把既有 async circuit/pacing controller 安全橋接到 one-shot 與 sync
fallback，並維持 breaker preflight → OS profile lease → persisted pacing →
browser work 的順序；本模組不建立第二套 breaker 或 pacing owner。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.managed_profile_identity import (
    resolve_managed_profile_identity,
)
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.core.defaults import PYTHON_FACEBOOK_AUTOMATION_DEFAULTS
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.runtime.paths import FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db,
)
from facebook_monitor.worker.facebook_access_runtime_gate import FacebookAccessRuntimeGate
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookGovernedAutomationLease,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationCoordinator,
)
from facebook_monitor.worker.facebook_automation_coordinator import (
    FacebookAutomationWorkKind,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardError,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardRuntime,
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
from facebook_monitor.worker.facebook_visible_write import (
    fenced_facebook_application_context,
)
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard


ProfileLeaseFactory = Callable[[Path, str], AbstractContextManager[object]]


class FacebookFallbackWorkDeferred(WorkerFailure):
    """表示 fallback Facebook work 被 circuit/pacing 正常延後，而非掃描失敗。"""


class FacebookFallbackIncidentRecorded(WorkerFailure):
    """表示 temporary block 已交由 profile incident transaction 完整處理。"""


class FacebookFallbackIncidentPersistenceDeferred(WorkerFailure):
    """表示 trip 已關閉 normal writes，但 incident DB commit 尚未確認。"""


@dataclass
class GovernedFallbackPostsWork:
    """保存已同時取得 circuit token、persistent pacing 與 profile lease 的 work。"""

    lease: FacebookGovernedAutomationLease
    profile_scope_key: str
    controller: FacebookAutomationAdmissionController
    _browser_context_closed: bool = False

    @property
    def browser_context_closed(self) -> bool:
        """回傳 caller 是否已確認本次 browser context 完成關閉。"""

        return self._browser_context_closed

    def note_browser_context_closed(self) -> None:
        """由 browser context owner 在 close 成功後回報，允許清除 durable marker。"""

        self._browser_context_closed = True

    def application_context(self) -> AbstractContextManager[ApplicationContext]:
        """回傳 success/skip visible commit 使用的 generation/process fence。"""

        return fenced_facebook_application_context(
            db_path=self.controller.db_path,
            controller=self.controller,
            token=self.lease.admission_token,
        )

    def record_temporary_block_incident(
        self,
        *,
        error: BaseException,
        target_id: str,
        commit_guard: ScanCommitGuard,
        worker_mode: WorkerMode,
    ) -> bool:
        """把 fallback page-guard temporary block 寫入同一 profile incident。"""

        if (
            not isinstance(error, WorkerFailure)
            or error.reason != FACEBOOK_TEMPORARY_BLOCK_REASON
        ):
            return False
        token = self.lease.admission_token
        if not self.controller.runtime_gate.request_trip(token):
            raise FacebookFallbackIncidentPersistenceDeferred(
                "deferred_breaker",
                "Facebook automation admission became stale before incident commit.",
            )
        try:
            self.controller.mark_session_guard_trip_pending(
                operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
            )
        except FacebookAutomationSessionGuardError as exc:
            raise FacebookFallbackIncidentPersistenceDeferred(
                "deferred_breaker",
                "Facebook automation trip sentinel could not be persisted.",
            ) from exc
        source_owner_token = self.lease.process_lease.operation_id
        try:
            outcome = record_facebook_access_incident_for_db(
                db_path=self.controller.db_path,
                signal=FacebookAccessBlockSignal(
                    admission_token=token,
                    source_kind=FacebookWorkSourceKind.SCAN,
                    operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                    trigger_action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
                    source_owner_token=source_owner_token,
                    target_id=target_id,
                    evidence_code="facebook_page_guard_v1",
                ),
                source_owner_token=source_owner_token,
                scan_commit_guard=commit_guard,
                diagnostics=(
                    error.diagnostics
                    if isinstance(error.diagnostics, WorkerFailureDiagnostics)
                    else None
                ),
                worker_mode=worker_mode,
            )
        except Exception as exc:
            raise FacebookFallbackIncidentPersistenceDeferred(
                "deferred_breaker",
                "Facebook access incident persistence is unconfirmed.",
            ) from exc
        if not outcome.committed:
            raise FacebookFallbackIncidentPersistenceDeferred(
                "deferred_breaker",
                f"Facebook access incident was not committed: {outcome.reason}",
            )
        self.controller.note_session_guard_incident_committed()
        return True


@contextmanager
def governed_fallback_posts_work(
    *,
    db_path: Path,
    profile_dir: Path,
    profile_lease_factory: ProfileLeaseFactory,
    profile_lease_owner: str,
    owner_alias: str,
) -> Iterator[GovernedFallbackPostsWork]:
    """取得一次涵蓋完整同步 POSTS Facebook work lifetime 的 governed lease。"""

    identity = resolve_managed_profile_identity(
        db_path=db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    session_guard_store = _session_guard_store(
        db_path=db_path,
        profile_dir=profile_dir,
        profile_scope_key=identity.profile_scope_key,
    )
    restart_guard = reconcile_facebook_automation_restart_guard(
        db_path=db_path,
        store=session_guard_store,
        profile_scope_key=identity.profile_scope_key,
    )
    if not restart_guard.browser_io_allowed:
        raise FacebookFallbackWorkDeferred(
            "deferred_breaker",
            restart_guard.reason_code or "Facebook automation restart guard is active.",
        )
    with SqliteApplicationContext(db_path) as app:
        circuit = app.services.facebook_access_circuit.get(identity.profile_scope_key)
    if circuit is not None and circuit.status != FacebookAccessCircuitStatus.CLOSED:
        raise FacebookFallbackWorkDeferred(
            "deferred_breaker",
            f"Facebook automation circuit is {circuit.status.value}.",
        )

    try:
        with profile_lease_factory(profile_dir, profile_lease_owner):
            with asyncio.Runner() as runner:
                controller = _build_controller(
                    db_path=db_path,
                    profile_scope_key=identity.profile_scope_key,
                    session_guard_store=session_guard_store,
                )
                runner.run(controller.recover_expired_pacing_lease())
                admission = runner.run(
                    controller.acquire(
                        work_kind=FacebookAutomationWorkKind.TARGET_SCAN,
                        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
                        owner_alias=owner_alias,
                    )
                )
                if not admission.admitted or admission.lease is None:
                    raise FacebookFallbackWorkDeferred(
                        admission.reason or "deferred_breaker",
                        "Facebook automation admission was deferred.",
                    )
                work = GovernedFallbackPostsWork(
                    lease=admission.lease,
                    profile_scope_key=identity.profile_scope_key,
                    controller=controller,
                )
                try:
                    controller.start_session_guard_before_browser_io()
                    yield work
                finally:
                    try:
                        if work.browser_context_closed:
                            controller.finish_session_guard_after_browser_context_closed()
                    finally:
                        runner.run(admission.lease.release())
    except ProfileLeaseError as exc:
        raise WorkerFailure(PROFILE_LOCKED_REASON, str(exc)) from exc


def _build_controller(
    *,
    db_path: Path,
    profile_scope_key: str,
    session_guard_store: FacebookAutomationSessionGuardStore,
) -> FacebookAutomationAdmissionController:
    """以正式安全預設建立同步 fallback 共用的既有 admission controller。"""

    defaults = PYTHON_FACEBOOK_AUTOMATION_DEFAULTS
    return FacebookAutomationAdmissionController(
        db_path=db_path,
        profile_scope_key=profile_scope_key,
        runtime_gate=FacebookAccessRuntimeGate(),
        coordinator=FacebookAutomationCoordinator(
            quiet_gap_min_seconds=defaults.quiet_gap_min_seconds,
            quiet_gap_max_seconds=defaults.quiet_gap_max_seconds,
        ),
        pacing_lease_seconds=defaults.persistent_lease_seconds,
        persistent_quiet_gap_seconds=defaults.persistent_quiet_gap_seconds,
        session_guard_runtime=FacebookAutomationSessionGuardRuntime(session_guard_store),
    )


def _session_guard_store(
    *,
    db_path: Path,
    profile_dir: Path,
    profile_scope_key: str,
) -> FacebookAutomationSessionGuardStore:
    """依正式 runtime layout 取得與 resident 共用的 profile sentinel store。"""

    profile_parent = profile_dir.expanduser().resolve().parent
    data_dir = (
        profile_parent.parent
        if profile_parent.name.casefold() == "profiles"
        else db_path.expanduser().resolve().parent
    )
    return FacebookAutomationSessionGuardStore(
        data_dir / FACEBOOK_AUTOMATION_SESSION_GUARDS_DIR_NAME,
        profile_alias=derive_facebook_automation_profile_alias(profile_scope_key),
    )


__all__ = [
    "FacebookFallbackIncidentPersistenceDeferred",
    "FacebookFallbackIncidentRecorded",
    "FacebookFallbackWorkDeferred",
    "GovernedFallbackPostsWork",
    "ProfileLeaseFactory",
    "governed_fallback_posts_work",
]
