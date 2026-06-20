"""Typed scan commit outcomes for resident worker hardening.

職責：描述 scan commit helper 的 side-effect result，供後續 thin
coordinator 使用；本模組不執行 DB、notification 或 scanner side effects。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum

from facebook_monitor.core.scan_failure_policy import ScanFailureDecision


class ScanCommitOutcomeKind(StrEnum):
    """scan commit helper 的內部結果分類。"""

    SUCCESS_COMMITTED = "success_committed"
    SKIP_COMMITTED = "skip_committed"
    FAILURE_COMMITTED = "failure_committed"
    GUARD_MISMATCH = "guard_mismatch"
    TARGET_INACTIVE = "target_inactive"
    SQLITE_LOCK_RETRY = "sqlite_lock_retry"


@dataclass(frozen=True)
class ScanCommitSideEffects:
    """描述 coordinator 本輪已完成的 scan commit side effects。"""

    wrote_scan_run: bool = False
    wrote_latest_scan: bool = False
    cleared_latest_scan: bool = False
    wrote_match_history: bool = False
    enqueued_match_notification_outbox: bool = False
    enqueued_runtime_failure_notification_outbox: bool = False
    updated_scope_state: bool = False
    updated_runtime_state: bool = False

    @property
    def any(self) -> bool:
        """回傳本物件是否記錄任一 side effect。"""

        return any(
            (
                self.wrote_scan_run,
                self.wrote_latest_scan,
                self.cleared_latest_scan,
                self.wrote_match_history,
                self.enqueued_match_notification_outbox,
                self.enqueued_runtime_failure_notification_outbox,
                self.updated_scope_state,
                self.updated_runtime_state,
            )
        )


@dataclass(frozen=True)
class ScanCommitOutcome:
    """保存 scan commit 結果與已完成 side-effect 摘要。"""

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
    side_effects: ScanCommitSideEffects = field(default_factory=ScanCommitSideEffects)

    @property
    def committed_visible_scan_state(self) -> bool:
        """回傳本次 outcome 是否已寫入 visible scan result。"""

        return self.side_effects.wrote_scan_run and self.kind in {
            ScanCommitOutcomeKind.SUCCESS_COMMITTED,
            ScanCommitOutcomeKind.SKIP_COMMITTED,
            ScanCommitOutcomeKind.FAILURE_COMMITTED,
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
    "ScanCommitSideEffects",
]
