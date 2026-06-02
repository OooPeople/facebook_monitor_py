"""Scan failure reason constants.

職責：集中跨 worker、scheduler 與 Web UI 共用的 scan failure reason，
避免不同層各自硬寫同一組狀態字串。
"""

CONTENT_UNAVAILABLE_REASON = "content_unavailable"
LOGIN_REQUIRED_REASON = "login_required"
CHECKPOINT_REQUIRED_REASON = "checkpoint_required"
SESSION_INVALID_REASON = "session_invalid"
PROFILE_LOCKED_REASON = "profile_locked"
PAGE_LOAD_TIMEOUT_REASON = "page_load_timeout"
SCHEDULER_RUNTIME_REASON = "scheduler_runtime"
STALE_RUNNING_REASON = "stale_running"
SORT_ADJUST_UNCONFIRMED_REASON = "sort_adjust_unconfirmed"
EXTRACTOR_EMPTY_REASON = "extractor_empty"
EXTRACTOR_RUNTIME_REASON = "extractor_runtime"
SCAN_TIMEOUT_REASON = "scan_timeout"
SCHEDULER_STOPPING_REASON = "scheduler_stopping"
TARGET_STOPPED_REASON = "target_stopped"
TARGET_MISSING_REASON = "target_missing"
TARGET_KIND_UNSUPPORTED_REASON = "target_kind_unsupported"
TARGET_INVALID_REASON = "target_invalid"
TARGET_ARGUMENT_CONFLICT_REASON = "target_argument_conflict"
PROFILE_MISSING_REASON = "profile_missing"
UNKNOWN_REASON = "unknown"

PROFILE_SESSION_FAILURE_REASONS = frozenset(
    {
        LOGIN_REQUIRED_REASON,
        CHECKPOINT_REQUIRED_REASON,
        SESSION_INVALID_REASON,
    }
)
