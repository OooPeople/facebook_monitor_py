"""Scan failure runtime decision policy。

職責：集中保存 worker / scheduler 失敗後的 runtime 處置規則，避免
resident main、fallback 與 one-shot 各自維護 retryable / error 判斷。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.scan_failures import CHECKPOINT_REQUIRED_REASON
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_MISSING_REASON
from facebook_monitor.core.scan_failures import SCHEDULER_RUNTIME_REASON
from facebook_monitor.core.scan_failures import SESSION_INVALID_REASON
from facebook_monitor.core.scan_failures import STALE_RUNNING_REASON
from facebook_monitor.core.scan_failures import TARGET_ARGUMENT_CONFLICT_REASON
from facebook_monitor.core.scan_failures import TARGET_INVALID_REASON
from facebook_monitor.core.scan_failures import TARGET_KIND_UNSUPPORTED_REASON
from facebook_monitor.core.scan_failures import TARGET_MISSING_REASON
from facebook_monitor.core.scan_failures import TARGET_STOPPED_REASON
from facebook_monitor.core.scan_failures import UNKNOWN_REASON


ScanFailureSource = Literal[
    "worker_failure",
    "playwright",
    "unknown_exception",
    "scheduler_cancel",
    "runtime_recovery",
]
TargetFailureAction = Literal["idle", "error"]
FailureRuntimeAction = Literal["idle", "will_retry", "error"]

IDLE_FAILURE_REASONS = frozenset({TARGET_STOPPED_REASON})
SCHEDULER_CANCEL_IDLE_FAILURE_REASONS = frozenset({"scheduler_stopping"})
IMMEDIATE_TERMINAL_FAILURE_REASONS = frozenset(
    {
        CHECKPOINT_REQUIRED_REASON,
        CONTENT_UNAVAILABLE_REASON,
        LOGIN_REQUIRED_REASON,
        PROFILE_LOCKED_REASON,
        PROFILE_MISSING_REASON,
        SESSION_INVALID_REASON,
        TARGET_ARGUMENT_CONFLICT_REASON,
        TARGET_INVALID_REASON,
        TARGET_KIND_UNSUPPORTED_REASON,
        TARGET_MISSING_REASON,
    }
)
STREAK_RETRY_FAILURE_LIMITS = {
    PAGE_LOAD_TIMEOUT_REASON: (
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.page_load_timeout_failure_limit
    ),
    STALE_RUNNING_REASON: (
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.stale_running_failure_limit
    ),
    SCHEDULER_RUNTIME_REASON: (
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scheduler_runtime_failure_limit
    ),
}
AUTO_RESTART_FAILURE_ACTIONS = {
    PAGE_LOAD_TIMEOUT_REASON: "target_page_restart",
    STALE_RUNNING_REASON: "target_page_restart",
    SCHEDULER_RUNTIME_REASON: "scheduler_runtime_restart",
}
DEFAULT_AUTO_RESTART_ACTION = "target_page_restart"
DISCARD_PAGE_FAILURE_SOURCES = frozenset(
    {"playwright", "unknown_exception", "runtime_recovery"}
)


@dataclass(frozen=True)
class ScanFailureDecision:
    """保存一次 scan failure 對 runtime state 與 diagnostics 的處置結果。"""

    reason: str
    retryable: bool
    target_action: TargetFailureAction
    runtime_action: FailureRuntimeAction
    discard_page: bool
    counts_toward_streak: bool = False
    retry_streak: int = 0
    retry_limit: int = 0
    auto_restart: bool = False
    recovery_action: str = ""

    @property
    def terminal(self) -> bool:
        """回傳本次失敗是否已讓 target 進入 error 停止排程。"""

        return self.target_action == "error"


def decide_scan_failure(
    reason: str,
    *,
    source: ScanFailureSource,
    previous_failure_reason: str = "",
    previous_failure_count: int = 0,
) -> ScanFailureDecision:
    """依 reason、來源與既有連續失敗狀態決定本輪失敗後的處置。"""

    normalized_reason = str(reason or UNKNOWN_REASON).strip() or UNKNOWN_REASON
    discard_page = source in DISCARD_PAGE_FAILURE_SOURCES
    if (
        source == "scheduler_cancel"
        and normalized_reason in SCHEDULER_CANCEL_IDLE_FAILURE_REASONS
    ):
        return ScanFailureDecision(
            reason=normalized_reason,
            retryable=True,
            target_action="idle",
            runtime_action="idle",
            discard_page=discard_page,
        )
    if normalized_reason in IDLE_FAILURE_REASONS:
        return ScanFailureDecision(
            reason=normalized_reason,
            retryable=True,
            target_action="idle",
            runtime_action="idle",
            discard_page=discard_page,
        )
    if normalized_reason in IMMEDIATE_TERMINAL_FAILURE_REASONS:
        return ScanFailureDecision(
            reason=normalized_reason,
            retryable=False,
            target_action="error",
            runtime_action="error",
            discard_page=discard_page,
        )

    retry_limit = _retry_limit_for_reason(normalized_reason)
    retry_streak = _next_retry_streak(
        reason=normalized_reason,
        previous_failure_reason=previous_failure_reason,
        previous_failure_count=previous_failure_count,
    )
    will_retry = retry_streak < retry_limit
    recovery_action = AUTO_RESTART_FAILURE_ACTIONS.get(
        normalized_reason,
        DEFAULT_AUTO_RESTART_ACTION,
    )
    return ScanFailureDecision(
        reason=normalized_reason,
        retryable=will_retry,
        target_action="idle" if will_retry else "error",
        runtime_action="will_retry" if will_retry else "error",
        discard_page=discard_page or recovery_action == "target_page_restart",
        counts_toward_streak=True,
        retry_streak=retry_streak,
        retry_limit=retry_limit,
        auto_restart=will_retry,
        recovery_action=recovery_action,
    )


def _next_retry_streak(
    *,
    reason: str,
    previous_failure_reason: str,
    previous_failure_count: int,
) -> int:
    """計算同一 reason 的下一個連續失敗次數。"""

    if previous_failure_reason == reason:
        return max(previous_failure_count, 0) + 1
    return 1


def _retry_limit_for_reason(reason: str) -> int:
    """回傳指定 reason 的連續失敗 retry 上限。"""

    return STREAK_RETRY_FAILURE_LIMITS.get(
        reason,
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.recoverable_failure_limit,
    )
