"""Facebook access circuit 專用 incident transaction。

職責：在單一 ``BEGIN IMMEDIATE`` transaction 內重驗來源 owner、trip profile
circuit，並只為合法 scan owner 寫入一筆 canonical blocked scan 與收斂 runtime。
本模組刻意不使用一般 failure decision、notification 或 product item finalize。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import logging
from pathlib import Path
import re

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scan_recording_service import RecordScanRequest
from facebook_monitor.core.facebook_access import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookCircuitTripOutcome
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.user_messages import format_failure_message
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry_async
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.failure_diagnostics import (
    serialize_worker_failure_diagnostics,
)
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.scan_commit_guard import runtime_state_matches_commit_guard


logger = logging.getLogger(__name__)
_SAFE_EVIDENCE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_INCIDENT_SOURCES = frozenset(
    {
        FacebookWorkSourceKind.SCAN,
        FacebookWorkSourceKind.METADATA,
        FacebookWorkSourceKind.COVER,
    }
)


class FacebookAccessIncidentOutcomeKind(StrEnum):
    """專用 incident transaction 的穩定結果分類。"""

    OPENED = "opened"
    REPEATED = "repeated"
    REJECTED_SOURCE = "rejected_source"
    REJECTED_SOURCE_OWNER = "rejected_source_owner"
    REJECTED_SCAN_OWNER = "rejected_scan_owner"
    REJECTED_SIGNAL = "rejected_signal"
    REJECTED_STALE_ADMISSION = "rejected_stale_admission"


@dataclass(frozen=True)
class FacebookAccessIncidentOutcome:
    """回傳 incident 是否持久化及唯一允許的 scan side effect。"""

    kind: FacebookAccessIncidentOutcomeKind
    trip_outcome: FacebookCircuitTripOutcome | None = None
    scan_run_id: int = 0
    runtime_released: bool = False
    circuit_generation: int = 0
    reason: str = ""

    @property
    def committed(self) -> bool:
        """只有 opened/repeated 代表 circuit incident 已成功持久化。"""

        return self.kind in {
            FacebookAccessIncidentOutcomeKind.OPENED,
            FacebookAccessIncidentOutcomeKind.REPEATED,
        }


def record_facebook_access_incident_for_db(
    *,
    db_path: Path,
    signal: FacebookAccessBlockSignal,
    source_owner_token: str,
    scan_commit_guard: ScanCommitGuard | None = None,
    diagnostics: WorkerFailureDiagnostics | None = None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    occurred_at: datetime | None = None,
) -> FacebookAccessIncidentOutcome:
    """以 bounded SQLite retry 執行完整 incident transaction。

    ``source_owner_token`` 應傳目前 coordinator lease ``operation_id``，且 signal
    必須攜帶同一值；SCAN 另強制以 DB runtime row 重驗 ``scan_commit_guard``。
    """

    def operation() -> FacebookAccessIncidentOutcome:
        return _record_facebook_access_incident_once(
            db_path=db_path,
            signal=signal,
            source_owner_token=source_owner_token,
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
    signal: FacebookAccessBlockSignal,
    source_owner_token: str,
    scan_commit_guard: ScanCommitGuard | None = None,
    diagnostics: WorkerFailureDiagnostics | None = None,
    worker_mode: WorkerMode = WorkerMode.HEADLESS,
    occurred_at: datetime | None = None,
) -> FacebookAccessIncidentOutcome:
    """Async resident 使用的 thread-offloaded bounded retry wrapper。"""

    def operation() -> FacebookAccessIncidentOutcome:
        return _record_facebook_access_incident_once(
            db_path=db_path,
            signal=signal,
            source_owner_token=source_owner_token,
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
    signal: FacebookAccessBlockSignal,
    source_owner_token: str,
    scan_commit_guard: ScanCommitGuard | None,
    diagnostics: WorkerFailureDiagnostics | None,
    worker_mode: WorkerMode,
    occurred_at: datetime | None,
) -> FacebookAccessIncidentOutcome:
    """建立 application context，明確切出唯一 incident write transaction。"""

    # Admission token 只能來自已初始化 DB；incident 不在 safety-critical 路徑跑 migration。
    with SqliteApplicationContext(db_path, initialize_schema_on_enter=False) as app:
        connection = app.repositories.facebook_access_circuit.connection
        # Context bootstrap/secret repair 不是 incident 的一部分，先收斂後再取得 writer lock。
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        return _record_facebook_access_incident(
            app=app,
            signal=signal,
            source_owner_token=source_owner_token,
            scan_commit_guard=scan_commit_guard,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
            occurred_at=occurred_at or utc_now(),
        )


def _record_facebook_access_incident(
    *,
    app: ApplicationContext,
    signal: FacebookAccessBlockSignal,
    source_owner_token: str,
    scan_commit_guard: ScanCommitGuard | None,
    diagnostics: WorkerFailureDiagnostics | None,
    worker_mode: WorkerMode,
    occurred_at: datetime,
) -> FacebookAccessIncidentOutcome:
    """在已取得 immediate writer transaction 內執行 owner-first incident。"""

    if signal.source_kind not in _INCIDENT_SOURCES:
        return _rejected(
            FacebookAccessIncidentOutcomeKind.REJECTED_SOURCE,
            "facebook_access_incident_source_unsupported",
        )
    normalized_owner = str(source_owner_token or "").strip()
    if not normalized_owner or normalized_owner != signal.source_owner_token.strip():
        return _rejected(
            FacebookAccessIncidentOutcomeKind.REJECTED_SOURCE_OWNER,
            "facebook_access_incident_source_owner_mismatch",
        )
    if not _SAFE_EVIDENCE_CODE.fullmatch(signal.evidence_code):
        return _rejected(
            FacebookAccessIncidentOutcomeKind.REJECTED_SIGNAL,
            "facebook_access_incident_evidence_invalid",
        )

    scan_owner = None
    if signal.source_kind == FacebookWorkSourceKind.SCAN:
        scan_owner = _load_valid_scan_owner(
            app=app,
            signal=signal,
            scan_commit_guard=scan_commit_guard,
        )
        if scan_owner is None:
            return _rejected(
                FacebookAccessIncidentOutcomeKind.REJECTED_SCAN_OWNER,
                "facebook_access_incident_scan_owner_mismatch",
            )

    trip = app.services.facebook_access_circuit.trip(
        signal,
        source_owner_is_valid=True,
        detected_at=occurred_at,
    )
    rejected = _trip_rejection_outcome(trip.outcome, trip.state.generation)
    if rejected is not None:
        return rejected

    scan_run_id = 0
    runtime_released = False
    if scan_owner is not None and scan_commit_guard is not None:
        scan_run_id = _record_blocked_scan(
            app=app,
            target=scan_owner[0],
            signal=signal,
            diagnostics=diagnostics,
            worker_mode=worker_mode,
        )
        runtime_released = _release_scan_runtime_owner(
            app=app,
            target_id=scan_owner[0].id,
            commit_guard=scan_commit_guard,
            occurred_at=occurred_at,
        )
        if not runtime_released:
            raise RuntimeError("Facebook access incident lost scan owner inside transaction")

    kind = (
        FacebookAccessIncidentOutcomeKind.OPENED
        if trip.outcome == FacebookCircuitTripOutcome.OPENED
        else FacebookAccessIncidentOutcomeKind.REPEATED
    )
    return FacebookAccessIncidentOutcome(
        kind=kind,
        trip_outcome=trip.outcome,
        scan_run_id=scan_run_id,
        runtime_released=runtime_released,
        circuit_generation=trip.state.generation,
        reason=FACEBOOK_TEMPORARY_BLOCK_REASON,
    )


def _load_valid_scan_owner(
    *,
    app: ApplicationContext,
    signal: FacebookAccessBlockSignal,
    scan_commit_guard: ScanCommitGuard | None,
) -> tuple[TargetDescriptor, TargetRuntimeState] | None:
    """在 writer transaction 內驗 target intent 與 running attempt identity。"""

    target_id = str(signal.target_id or "").strip()
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
    signal: FacebookAccessBlockSignal,
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
        "source_kind": signal.source_kind.value,
        "operation_kind": signal.operation_kind.value,
        "trigger_action_kind": signal.trigger_action_kind.value,
        "evidence_code": signal.evidence_code,
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


def _release_scan_runtime_owner(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard,
    occurred_at: datetime,
) -> bool:
    """Narrow guarded UPDATE 回 idle；保留 desired intent、streak 與新 scan request。"""

    occurred_at_text = encode_datetime(occurred_at)
    started_at_text = encode_datetime(commit_guard.started_at)
    cursor = app.repositories.runtime_states.connection.execute(
        """
        UPDATE target_runtime_state
        SET runtime_status = ?,
            scan_requested_at = CASE
                WHEN scan_requested_at <> '' AND scan_requested_at <= ? THEN ''
                ELSE scan_requested_at
            END,
            last_finished_at = ?,
            last_skip_reason = ?,
            enqueue_reason = '',
            active_worker_id = '',
            active_page_id = '',
            display_next_due_at = '',
            updated_at = ?
        WHERE target_id = ?
          AND desired_state = ?
          AND runtime_status = ?
          AND active_worker_id = ?
          AND last_started_at = ?
          AND (? = '' OR active_page_id = ?)
        """,
        (
            TargetRuntimeStatus.IDLE.value,
            started_at_text,
            occurred_at_text,
            FACEBOOK_TEMPORARY_BLOCK_REASON,
            occurred_at_text,
            target_id,
            TargetDesiredState.ACTIVE.value,
            TargetRuntimeStatus.RUNNING.value,
            commit_guard.worker_id,
            started_at_text,
            commit_guard.page_id,
            commit_guard.page_id,
        ),
    )
    return cursor.rowcount == 1


def _trip_rejection_outcome(
    outcome: FacebookCircuitTripOutcome,
    generation: int,
) -> FacebookAccessIncidentOutcome | None:
    """將 circuit service rejection 轉成 incident typed outcome。"""

    mapping = {
        FacebookCircuitTripOutcome.REJECTED_OWNER: (
            FacebookAccessIncidentOutcomeKind.REJECTED_SOURCE_OWNER,
            "facebook_access_incident_source_owner_mismatch",
        ),
        FacebookCircuitTripOutcome.REJECTED_SIGNAL: (
            FacebookAccessIncidentOutcomeKind.REJECTED_SIGNAL,
            "facebook_access_incident_signal_invalid",
        ),
        FacebookCircuitTripOutcome.REJECTED_STALE_ADMISSION: (
            FacebookAccessIncidentOutcomeKind.REJECTED_STALE_ADMISSION,
            "facebook_access_incident_stale_admission",
        ),
    }
    rejected = mapping.get(outcome)
    if rejected is None:
        return None
    return FacebookAccessIncidentOutcome(
        kind=rejected[0],
        trip_outcome=outcome,
        circuit_generation=generation,
        reason=rejected[1],
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
