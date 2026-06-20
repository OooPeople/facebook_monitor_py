"""Resident maintenance refresh eligibility helpers。"""

from __future__ import annotations

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


def filter_maintenance_refresh_target_ids(
    options: ResidentRuntimeOptions,
    target_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """避開已有正式掃描工作的 target，避免 maintenance job 擋住 retry。"""

    if not target_ids:
        return ()
    with SqliteApplicationContext(options.db_path) as app:
        runtime_states = app.repositories.runtime_states.list_by_targets(list(target_ids))
        targets = {
            target_id: app.repositories.targets.get(target_id)
            for target_id in target_ids
        }
    return tuple(
        target_id
        for target_id in target_ids
        if targets.get(target_id) is not None
        and _runtime_state_allows_maintenance_refresh(runtime_states.get(target_id))
    )


def filter_maintenance_cover_refresh_states(
    options: ResidentRuntimeOptions,
    states: list[TargetCoverImageRefreshState],
) -> list[TargetCoverImageRefreshState]:
    """避開已有正式掃描工作的 cover refresh jobs。"""

    if not states:
        return []
    target_ids = [state.target_id for state in states]
    with SqliteApplicationContext(options.db_path) as app:
        runtime_states = app.repositories.runtime_states.list_by_targets(target_ids)
        targets = {
            target_id: app.repositories.targets.get(target_id)
            for target_id in target_ids
        }
    return [
        state
        for state in states
        if targets.get(state.target_id) is not None
        and _runtime_state_allows_maintenance_refresh(runtime_states.get(state.target_id))
    ]


def _runtime_state_allows_maintenance_refresh(
    state: TargetRuntimeState | None,
) -> bool:
    """runtime recovery retry 等待期間，maintenance refresh 先讓位。"""

    if state is None:
        return True
    if _runtime_state_has_pending_failure_retry(state):
        return False
    return state.runtime_status not in {
        TargetRuntimeStatus.QUEUED,
        TargetRuntimeStatus.RUNNING,
        TargetRuntimeStatus.ERROR,
    }


def _runtime_state_has_pending_failure_retry(state: TargetRuntimeState) -> bool:
    """判斷 target 是否正等待 failure policy 自動重試掃描。"""

    return (
        state.runtime_status == TargetRuntimeStatus.IDLE
        and state.scan_requested_at is not None
        and state.consecutive_failure_count > 0
    )
