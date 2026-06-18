"""Resident attempt typed outcome adapters.

職責：讓 resident executor 內部可以用明確 outcome 描述終端結果，
但對外仍維持既有 AsyncTargetScanResult 與 counters 語義。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from facebook_monitor.worker.resident_main_executor_types import AsyncTargetScanResult


class ResidentAttemptOutcomeKind(StrEnum):
    """resident queue attempt 的內部終端分類。"""

    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OWNER_CHANGED = "owner_changed"
    TARGET_INACTIVE = "target_inactive"
    SQLITE_LOCK_RETRY = "sqlite_lock_retry"
    RUNTIME_RESTART_REQUESTED = "runtime_restart_requested"


@dataclass(frozen=True)
class ResidentAttemptOutcome:
    """保存 resident attempt 結果，並可轉回既有 executor result model。"""

    kind: ResidentAttemptOutcomeKind
    target_id: str
    reason: str = ""
    source: str = ""
    exception_class: str = ""
    request_runtime_restart: bool = False
    opened_page: bool = False
    reused_page: bool = False

    @classmethod
    def succeeded(
        cls,
        *,
        target_id: str,
        opened_page: bool,
        reused_page: bool,
    ) -> ResidentAttemptOutcome:
        """建立成功 outcome。"""

        return cls(
            kind=ResidentAttemptOutcomeKind.SUCCEEDED,
            target_id=target_id,
            opened_page=opened_page,
            reused_page=reused_page,
        )

    @classmethod
    def skipped(
        cls,
        *,
        target_id: str,
        kind: ResidentAttemptOutcomeKind = ResidentAttemptOutcomeKind.SKIPPED,
        reason: str = "",
        opened_page: bool = False,
        reused_page: bool = False,
    ) -> ResidentAttemptOutcome:
        """建立對外仍映射成 skipped 的 outcome。"""

        if kind not in _SKIPPED_RESULT_KINDS:
            raise ValueError(f"{kind.value} does not map to a skipped scan result")
        return cls(
            kind=kind,
            target_id=target_id,
            reason=reason,
            opened_page=opened_page,
            reused_page=reused_page,
        )

    @classmethod
    def failed(
        cls,
        *,
        target_id: str,
        reason: str = "",
        source: str = "",
        exception_class: str = "",
        request_runtime_restart: bool = False,
        opened_page: bool = False,
        reused_page: bool = False,
    ) -> ResidentAttemptOutcome:
        """建立 failure outcome。"""

        return cls(
            kind=ResidentAttemptOutcomeKind.FAILED,
            target_id=target_id,
            reason=reason,
            source=source,
            exception_class=exception_class,
            request_runtime_restart=request_runtime_restart,
            opened_page=opened_page,
            reused_page=reused_page,
        )

    @classmethod
    def runtime_restart_requested(
        cls,
        *,
        target_id: str,
        reason: str = "",
        source: str = "",
        exception_class: str = "",
        request_runtime_restart: bool = True,
        opened_page: bool = False,
        reused_page: bool = False,
    ) -> ResidentAttemptOutcome:
        """建立需要重建 browser runtime 的 failure outcome。"""

        return cls(
            kind=ResidentAttemptOutcomeKind.RUNTIME_RESTART_REQUESTED,
            target_id=target_id,
            reason=reason,
            source=source,
            exception_class=exception_class,
            request_runtime_restart=request_runtime_restart,
            opened_page=opened_page,
            reused_page=reused_page,
        )

    def to_scan_result(self) -> AsyncTargetScanResult:
        """轉成 executor 既有 counters 使用的 public result。"""

        if self.kind == ResidentAttemptOutcomeKind.SUCCEEDED:
            return AsyncTargetScanResult(
                target_id=self.target_id,
                success=True,
                opened_page=self.opened_page,
                reused_page=self.reused_page,
            )
        if self.kind in _FAILURE_RESULT_KINDS:
            return AsyncTargetScanResult(
                target_id=self.target_id,
                failure=True,
                opened_page=self.opened_page,
                reused_page=self.reused_page,
            )
        return AsyncTargetScanResult(
            target_id=self.target_id,
            skipped=True,
            opened_page=self.opened_page,
            reused_page=self.reused_page,
        )


_SKIPPED_RESULT_KINDS = frozenset(
    {
        ResidentAttemptOutcomeKind.SKIPPED,
        ResidentAttemptOutcomeKind.CANCELLED,
        ResidentAttemptOutcomeKind.OWNER_CHANGED,
        ResidentAttemptOutcomeKind.TARGET_INACTIVE,
        ResidentAttemptOutcomeKind.SQLITE_LOCK_RETRY,
    }
)
_FAILURE_RESULT_KINDS = frozenset(
    {
        ResidentAttemptOutcomeKind.FAILED,
        ResidentAttemptOutcomeKind.RUNTIME_RESTART_REQUESTED,
    }
)


__all__ = [
    "ResidentAttemptOutcome",
    "ResidentAttemptOutcomeKind",
]
