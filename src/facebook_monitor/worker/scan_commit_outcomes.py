"""Typed scan commit outcomes for resident worker hardening.

職責：描述 scan commit helper 的 side-effect result，供後續 thin
coordinator 使用；本模組不執行 DB、notification 或 scanner side effects。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from facebook_monitor.core.scan_failure_policy import ScanFailureDecision


class ScanCommitOutcomeKind(StrEnum):
    """scan commit helper 的內部結果分類。"""

    SUCCESS_COMMITTED = "success_committed"
    IDLE_COMMITTED = "idle_committed"
    SKIP_COMMITTED = "skip_committed"
    FAILURE_COMMITTED = "failure_committed"
    GUARD_MISMATCH = "guard_mismatch"
    TARGET_INACTIVE = "target_inactive"
    SQLITE_LOCK_RETRY = "sqlite_lock_retry"


@dataclass(frozen=True)
class ScanCommitOutcome:
    """保存 scan commit 結果，不包含任何 runtime side effect。"""

    kind: ScanCommitOutcomeKind
    target_id: str
    reason: str = ""
    scan_run_id: int = 0
    matched_count: int = 0
    new_count: int = 0
    request_runtime_restart: bool = False
    discard_page: bool = False
    failure_decision: ScanFailureDecision | None = None
    runtime_failure_notification_count: int = 0

    @property
    def committed_visible_scan_state(self) -> bool:
        """回傳本次 outcome 是否已寫入 visible scan result。"""

        if self.kind == ScanCommitOutcomeKind.FAILURE_COMMITTED:
            return self.scan_run_id > 0
        return self.kind in {
            ScanCommitOutcomeKind.SUCCESS_COMMITTED,
            ScanCommitOutcomeKind.SKIP_COMMITTED,
        }

    @property
    def stale_or_inactive(self) -> bool:
        """回傳本次 outcome 是否代表 stale owner 或 target inactive。"""

        return self.kind in {
            ScanCommitOutcomeKind.GUARD_MISMATCH,
            ScanCommitOutcomeKind.TARGET_INACTIVE,
        }


__all__ = [
    "ScanCommitOutcome",
    "ScanCommitOutcomeKind",
]
