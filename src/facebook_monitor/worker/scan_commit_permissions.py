"""Scan commit 前的 runtime guard 與 target 狀態分類 helper。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.scan_commit_guard import begin_scan_commit_transaction


class ScanCommitPermissionKind(StrEnum):
    """coordinator 使用的 commit guard 判斷分類。"""

    ALLOWED = "allowed"
    TARGET_INACTIVE = "target_inactive"
    GUARD_MISMATCH = "guard_mismatch"


@dataclass(frozen=True)
class ScanCommitPermission:
    """保存 commit 前 guard 判斷結果與可觀測 reason。"""

    kind: ScanCommitPermissionKind
    reason: str = ""

    @property
    def allowed(self) -> bool:
        """回傳本輪是否允許寫入 scan commit side effect。"""

        return self.kind == ScanCommitPermissionKind.ALLOWED


def classify_scan_commit_permission(
    *,
    app: ApplicationContext,
    target_id: str,
    commit_guard: ScanCommitGuard | None,
) -> ScanCommitPermission:
    """判斷 target 是否仍允許本輪 coordinator 寫入 scan side effect。"""

    begin_scan_commit_transaction(app)
    target = app.repositories.targets.get(target_id)
    if target is None:
        return ScanCommitPermission(
            ScanCommitPermissionKind.TARGET_INACTIVE,
            "target_missing_before_commit",
        )
    if not target.enabled or target.paused:
        return ScanCommitPermission(
            ScanCommitPermissionKind.TARGET_INACTIVE,
            "target_inactive_before_commit",
        )
    runtime_state = app.repositories.runtime_states.get(target_id)
    if runtime_state is None:
        return ScanCommitPermission(
            ScanCommitPermissionKind.GUARD_MISMATCH,
            "runtime_state_missing_before_commit",
        )
    if runtime_state.desired_state != TargetDesiredState.ACTIVE:
        return ScanCommitPermission(
            ScanCommitPermissionKind.TARGET_INACTIVE,
            "target_inactive_before_commit",
        )
    return classify_runtime_commit_guard(runtime_state, commit_guard)


def classify_runtime_commit_guard(
    runtime_state: TargetRuntimeState,
    commit_guard: ScanCommitGuard | None,
) -> ScanCommitPermission:
    """比對 running runtime owner 是否仍符合本輪 commit guard。"""

    if commit_guard is None:
        return ScanCommitPermission(ScanCommitPermissionKind.ALLOWED)
    if runtime_state.runtime_status != TargetRuntimeStatus.RUNNING:
        return ScanCommitPermission(
            ScanCommitPermissionKind.GUARD_MISMATCH,
            "runtime_not_running_before_commit",
        )
    if runtime_state.active_worker_id != commit_guard.worker_id:
        return ScanCommitPermission(
            ScanCommitPermissionKind.GUARD_MISMATCH,
            "owner_changed_before_commit",
        )
    if runtime_state.last_started_at != commit_guard.started_at:
        return ScanCommitPermission(
            ScanCommitPermissionKind.GUARD_MISMATCH,
            "scan_started_at_changed_before_commit",
        )
    if commit_guard.page_id and runtime_state.active_page_id != commit_guard.page_id:
        return ScanCommitPermission(
            ScanCommitPermissionKind.GUARD_MISMATCH,
            "page_owner_changed_before_commit",
        )
    return ScanCommitPermission(ScanCommitPermissionKind.ALLOWED)


__all__ = [
    "ScanCommitPermission",
    "ScanCommitPermissionKind",
    "classify_runtime_commit_guard",
    "classify_scan_commit_permission",
]
