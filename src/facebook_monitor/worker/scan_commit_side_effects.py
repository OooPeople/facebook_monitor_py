"""Scan commit outcome 使用的 side-effect 摘要建構 helper。"""

from __future__ import annotations

from facebook_monitor.worker.scan_commit_outcomes import ScanCommitSideEffects
from facebook_monitor.worker.scan_failure_finalize import GuardedScanFailureFinalizeResult
from facebook_monitor.worker.scan_finalize import ScanFinalizeResult


def side_effects_for_success(result: ScanFinalizeResult) -> ScanCommitSideEffects:
    """從 success finalize result 建立實際 side-effect 摘要。"""

    return ScanCommitSideEffects(
        wrote_scan_run=result.scan_run_id > 0,
        wrote_latest_scan=result.scan_run_id > 0,
        wrote_match_history=bool(result.history_entries),
        enqueued_match_notification_outbox=result.match_notification_outbox_count > 0,
        updated_scope_state=not result.baseline_mode or bool(result.match_results),
        updated_runtime_state=True,
    )


def side_effects_for_protective_skip(
    result: ScanFinalizeResult,
) -> ScanCommitSideEffects:
    """從 protective skip finalize result 建立實際 side-effect 摘要。"""

    return ScanCommitSideEffects(
        wrote_scan_run=result.scan_run_id > 0,
        cleared_latest_scan=True,
        updated_runtime_state=True,
    )


def side_effects_for_failure(
    result: GuardedScanFailureFinalizeResult,
) -> ScanCommitSideEffects:
    """從 guarded failure finalize result 建立實際 side-effect 摘要。"""

    return ScanCommitSideEffects(
        wrote_scan_run=result.scan_run_id > 0,
        enqueued_runtime_failure_notification_outbox=(
            result.runtime_failure_notification_count > 0
        ),
        updated_runtime_state=True,
    )


__all__ = [
    "side_effects_for_failure",
    "side_effects_for_protective_skip",
    "side_effects_for_success",
]
