"""Facebook temporary-block 專用 incident transaction。

職責：在單一 ``BEGIN IMMEDIATE`` transaction 內重驗來源 guard、保存 warning、
停止所有 active targets，並只為合法 scan owner 寫入 canonical blocked scan。
本模組刻意不使用一般 failure decision、notification 或 product item finalize。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import logging
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry_async
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.failure_diagnostics import (
    serialize_worker_failure_diagnostics,
)
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.scan_commit_guard import runtime_state_matches_commit_guard


logger = logging.getLogger(__name__)


class FacebookAccessIncidentOutcomeKind(StrEnum):
    """專用 incident transaction 的穩定結果分類。"""

    RECORDED = "recorded"
    REJECTED_SIGNAL = "rejected_signal"


@dataclass(frozen=True)
class FacebookAccessIncidentOutcome:
    """回傳 incident 是否持久化及唯一允許的 scan side effect。"""

    kind: FacebookAccessIncidentOutcomeKind
    scan_run_id: int = 0
    warning_generation: int = 0
    reason: str = ""

    @property
    def committed(self) -> bool:
        """只有 recorded 代表 warning 與 pause-all 已成功持久化。"""

        return self.kind == FacebookAccessIncidentOutcomeKind.RECORDED


def record_facebook_access_incident_for_db(
    *,
    db_path: Path,
    finding: TemporaryBlockFinding,
    scan_commit_guard: ScanCommitGuard | None = None,
    diagnostics: WorkerFailureDiagnostics | None = None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    occurred_at: datetime | None = None,
) -> FacebookAccessIncidentOutcome:
    """以 bounded SQLite retry 執行完整 incident transaction。"""

    def operation() -> FacebookAccessIncidentOutcome:
        return _record_facebook_access_incident_once(
            db_path=db_path,
            finding=finding,
            scan_commit_guard=scan_commit_guard,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
            occurred_at=occurred_at,
        )

    return run_sqlite_operation_with_retry(
        operation,
        operation_name="record_facebook_access_incident",
        logger=logger,
    )


async def record_facebook_access_incident_for_db_async(
    *,
    db_path: Path,
    finding: TemporaryBlockFinding,
    scan_commit_guard: ScanCommitGuard | None = None,
    diagnostics: WorkerFailureDiagnostics | None = None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    occurred_at: datetime | None = None,
) -> FacebookAccessIncidentOutcome:
    """Async resident 使用的 thread-offloaded bounded retry wrapper。"""

    def operation() -> FacebookAccessIncidentOutcome:
        return _record_facebook_access_incident_once(
            db_path=db_path,
            finding=finding,
            scan_commit_guard=scan_commit_guard,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
            occurred_at=occurred_at,
        )

    return await run_sqlite_operation_with_retry_async(
        operation,
        operation_name="record_facebook_access_incident",
        logger=logger,
    )


def _record_facebook_access_incident_once(
    *,
    db_path: Path,
    finding: TemporaryBlockFinding,
    scan_commit_guard: ScanCommitGuard | None,
    diagnostics: WorkerFailureDiagnostics | None,
    worker_mode: WorkerMode,
    occurred_at: datetime | None,
) -> FacebookAccessIncidentOutcome:
    """建立 application context，明確切出唯一 incident write transaction。"""

    # Incident 只接受已初始化 DB；正式 writer path 不在 transaction 內跑 migration。
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        connection = app.repositories.facebook_temporary_block_warning.connection
        # Context bootstrap/secret repair 不是 incident 的一部分，先收斂後再取得 writer lock。
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        return _record_facebook_access_incident(
            app=app,
            finding=finding,
            scan_commit_guard=scan_commit_guard,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
            occurred_at=occurred_at or utc_now(),
        )


def _record_facebook_access_incident(
    *,
    app: ApplicationContext,
    finding: TemporaryBlockFinding,
    scan_commit_guard: ScanCommitGuard | None,
    diagnostics: WorkerFailureDiagnostics | None,
    worker_mode: WorkerMode,
    occurred_at: datetime,
) -> FacebookAccessIncidentOutcome:
    """在已取得 immediate writer transaction 內驗證 finding 並保存 incident。"""

    scan_owner = None
    try:
        app.services.facebook_temporary_block_warning.validate(finding)
    except ValueError:
        return _rejected(
            FacebookAccessIncidentOutcomeKind.REJECTED_SIGNAL,
            "facebook_access_incident_signal_invalid",
        )

    if finding.source_kind == FacebookWorkSourceKind.SCAN:
        try:
            scan_owner = _load_valid_scan_owner(
                app=app,
                finding=finding,
                scan_commit_guard=scan_commit_guard,
            )
        except ValueError:
            # Runtime row 無法解碼時只略過附屬 blocked scan；confirmed finding
            # 仍必須保存 warning 並停止所有 active targets。
            scan_owner = None

    scan_run_id = 0
    if scan_owner is not None and scan_commit_guard is not None:
        scan_run_id = _record_blocked_scan(
            app=app,
            target=scan_owner[0],
            finding=finding,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
        )
    warning = app.services.facebook_temporary_block_warning.record(
        finding,
        detected_at=occurred_at,
    )
    app.services.targets.pause_all_target_monitoring()
    return FacebookAccessIncidentOutcome(
        kind=FacebookAccessIncidentOutcomeKind.RECORDED,
        scan_run_id=scan_run_id,
        warning_generation=warning.generation,
        reason=FACEBOOK_TEMPORARY_BLOCK_REASON,
    )


def _load_valid_scan_owner(
    *,
    app: ApplicationContext,
    finding: TemporaryBlockFinding,
    scan_commit_guard: ScanCommitGuard | None,
) -> tuple[TargetDescriptor, TargetRuntimeState] | None:
    """在 writer transaction 內驗 target intent 與 running attempt identity。"""

    target_id = str(finding.target_id or "").strip()
    if not target_id or scan_commit_guard is None:
        return None
    target = app.repositories.targets.get(target_id)
    if target is None or not target.enabled or target.paused:
        return None
    runtime_state = app.repositories.runtime_states.get(target_id)
    if runtime_state is None:
        return None
    if runtime_state.desired_state != TargetDesiredState.ACTIVE:
        return None
    if not runtime_state_matches_commit_guard(runtime_state, scan_commit_guard):
        return None
    return target, runtime_state


def _record_blocked_scan(
    *,
    app: ApplicationContext,
    target: TargetDescriptor,
    finding: TemporaryBlockFinding,
    diagnostics: WorkerFailureDiagnostics | None,
    worker_mode: WorkerMode,
) -> int:
    """寫入不含 raw URL/page text 且不觸發通知的 canonical failed scan。"""

    serialized = serialize_worker_failure_diagnostics(diagnostics)
    metadata: dict[str, object] = {
        "worker": "facebook_access_incident",
        "worker_mode": worker_mode.value,
        "target_kind": target.target_kind.value,
        "reason": FACEBOOK_TEMPORARY_BLOCK_REASON,
        "retryable": False,
        "runtime_action": "facebook_access_pause",
        "source_kind": finding.source_kind.value,
        "operation_kind": finding.operation_kind.value,
        "trigger_action_kind": finding.action_kind.value,
        "evidence_code": finding.evidence_code,
    }
    if serialized.payload:
        metadata["failure_diagnostics"] = serialized.payload
    elif not serialized.accepted:
        metadata["failure_diagnostics_status"] = serialized.status
    return app.services.scans.record_scan(
        RecordScanRequest(
            target_id=target.id,
            status=ScanStatus.FAILED,
            error_message=format_failure_message(FACEBOOK_TEMPORARY_BLOCK_REASON, ""),
            worker_mode=worker_mode,
            metadata=metadata,
        )
    )


def _rejected(
    kind: FacebookAccessIncidentOutcomeKind,
    reason: str,
) -> FacebookAccessIncidentOutcome:
    return FacebookAccessIncidentOutcome(kind=kind, reason=reason)


__all__ = [
    "FacebookAccessIncidentOutcome",
    "FacebookAccessIncidentOutcomeKind",
    "record_facebook_access_incident_for_db",
    "record_facebook_access_incident_for_db_async",
]
