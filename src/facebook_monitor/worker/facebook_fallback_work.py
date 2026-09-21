"""Single-target one-shot debug 的最小 Facebook work lifetime。

職責：只持有 OS profile lease、普通 application context 與 process-local
one-way trip signal；不承載 circuit、pacing、marker 或 visible-write fence。
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookAutomationRuntimeTripped,
)
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookAutomationTripSignal,
)
from facebook_monitor.worker.facebook_automation_runtime import (
    FacebookTemporaryBlockIncidentRecorded,
)
from facebook_monitor.worker.facebook_automation_runtime import record_temporary_block_sync
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard


ProfileLeaseFactory = Callable[[Path, str], AbstractContextManager[object]]


@dataclass
class FacebookFallbackWork:
    """保存單一 one-shot browser lifetime 的 DB path 與 one-way trip signal。"""

    db_path: Path
    signal: FacebookAutomationTripSignal

    def ensure_io_allowed(self) -> None:
        """runtime 已 trip 時拒絕後續 browser action。"""

        if self.signal.is_tripped():
            raise FacebookAutomationRuntimeTripped(
                "facebook fallback runtime is already tripped"
            )

    def application_context(self) -> AbstractContextManager[ApplicationContext]:
        """回傳不含 safety fence 的普通 SQLite application context。"""

        return SqliteApplicationContext(self.db_path)

    def record_temporary_block_incident(
        self,
        *,
        error: BaseException,
        target_id: str,
        commit_guard: ScanCommitGuard,
        worker_mode: WorkerMode,
        operation_kind: FacebookProductOperationKind,
        action_kind: FacebookActionKind,
    ) -> bool:
        """辨識 typed detector failure，寫入 incident 後要求 caller 停止 runtime。"""

        if (
            not isinstance(error, WorkerFailure)
            or error.reason != FACEBOOK_TEMPORARY_BLOCK_REASON
        ):
            return False
        diagnostics = (
            error.diagnostics
            if isinstance(error.diagnostics, WorkerFailureDiagnostics)
            else None
        )
        outcome = record_temporary_block_sync(
            signal=self.signal,
            db_path=self.db_path,
            finding=TemporaryBlockFinding(
                source_kind=FacebookWorkSourceKind.SCAN,
                operation_kind=operation_kind,
                action_kind=action_kind,
                target_id=target_id,
                evidence_code="facebook_page_guard_v1",
            ),
            scan_commit_guard=commit_guard,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
        )
        raise FacebookTemporaryBlockIncidentRecorded(outcome) from error


@contextmanager
def facebook_fallback_work(
    *,
    db_path: Path,
    profile_dir: Path,
    profile_lease_factory: ProfileLeaseFactory,
    profile_lease_owner: str,
    signal: FacebookAutomationTripSignal | None = None,
) -> Iterator[FacebookFallbackWork]:
    """取得只含 OS profile lease 與 process-local signal 的同步 lifetime。"""
    try:
        with profile_lease_factory(profile_dir, profile_lease_owner):
            yield FacebookFallbackWork(
                db_path=db_path,
                signal=signal or FacebookAutomationTripSignal(),
            )
    except ProfileLeaseError as exc:
        raise WorkerFailure(PROFILE_LOCKED_REASON, str(exc)) from exc


__all__ = [
    "FacebookFallbackWork",
    "ProfileLeaseFactory",
    "facebook_fallback_work",
]
