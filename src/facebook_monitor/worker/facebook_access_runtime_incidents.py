"""非 scan Facebook work 的 runtime incident adapter。"""

from __future__ import annotations

import logging
from pathlib import Path

from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db_async,
)
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookGovernedAutomationLease,
)
from facebook_monitor.worker.facebook_automation_session_guard import (
    FacebookAutomationSessionGuardError,
)


logger = logging.getLogger(__name__)


async def trip_non_scan_facebook_access_incident(
    *,
    db_path: Path,
    controller: FacebookAutomationAdmissionController | None,
    lease: FacebookGovernedAutomationLease | None,
    error: BaseException,
    source_kind: FacebookWorkSourceKind,
    operation_kind: FacebookProductOperationKind,
    target_id: str,
) -> bool:
    """將 metadata/cover high-confidence block 交給專用 transaction。"""

    if (
        controller is None
        or lease is None
        or not isinstance(error, WorkerFailure)
        or error.reason != FACEBOOK_TEMPORARY_BLOCK_REASON
    ):
        return False
    token = lease.admission_token
    if not controller.runtime_gate.request_trip(token):
        # 這仍是 high-confidence temporary block；token 失效代表同 process
        # 已先關閉 visible writes，不能再落入普通 metadata/cover failure write。
        logger.warning(
            "facebook_access_incident_stale_admission source=%s",
            source_kind.value,
        )
        return True
    action_kind = FacebookActionKind.GROUP_DOCUMENT
    try:
        controller.mark_session_guard_trip_pending(
            operation_kind=operation_kind,
            trigger_action_kind=action_kind,
        )
    except FacebookAutomationSessionGuardError:
        logger.exception(
            "facebook_access_session_guard_trip_pending_failed source=%s",
            source_kind.value,
        )
        return True
    source_owner_token = lease.process_lease.operation_id
    outcome = await record_facebook_access_incident_for_db_async(
        db_path=db_path,
        signal=FacebookAccessBlockSignal(
            admission_token=token,
            source_kind=source_kind,
            operation_kind=operation_kind,
            trigger_action_kind=action_kind,
            source_owner_token=source_owner_token,
            target_id=target_id,
            evidence_code="facebook_page_guard_v1",
        ),
        source_owner_token=source_owner_token,
        diagnostics=error.diagnostics,
    )
    if outcome.committed:
        controller.note_session_guard_incident_committed()
    return True


__all__ = ["trip_non_scan_facebook_access_incident"]
