"""Dashboard target error presentation helpers。"""

from __future__ import annotations

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import split_coded_message
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui


CONTENT_UNAVAILABLE_LABEL = "連結已失效"
CONTENT_UNAVAILABLE_TITLE = "Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
CONTENT_UNAVAILABLE_ERROR_MESSAGE = (
    "連結已失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
)
CONTENT_UNAVAILABLE_HISTORY_MESSAGE = (
    "曾偵測到連結失效：Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"
)


def is_content_unavailable_scan(scan: ScanRun | None) -> bool:
    """判斷 failed scan 是否代表 Facebook 內容不可見。"""

    if scan is None:
        return False
    metadata = scan.metadata or {}
    return (
        metadata.get("reason") == CONTENT_UNAVAILABLE_REASON
        or scan.error_message.startswith(f"{CONTENT_UNAVAILABLE_REASON}:")
        or scan.error_message.startswith(f"{CONTENT_UNAVAILABLE_LABEL}：")
    )


def is_content_unavailable_runtime_error(value: str) -> bool:
    """判斷 runtime error 是否代表 Facebook 內容不可見。"""

    code, _detail = split_coded_message(value)
    return code == CONTENT_UNAVAILABLE_REASON or value.startswith(
        f"{CONTENT_UNAVAILABLE_LABEL}："
    )


def is_retrying_failure_scan(scan: ScanRun | None) -> bool:
    """判斷 failed scan 是否為未達上限、將於下輪重試的失敗。"""

    if scan is None:
        return False
    metadata = scan.metadata or {}
    return bool(metadata.get("retryable")) and metadata.get("runtime_action") == "will_retry"


def format_retrying_failure_title(scan: ScanRun) -> str:
    """格式化可重試 failed scan 的 hover 說明。"""

    metadata = scan.metadata or {}
    retry_streak = metadata.get("retry_streak")
    retry_limit = metadata.get("retry_limit")
    if retry_streak and retry_limit:
        prefix = f"本輪掃描失敗，將於下輪重試（{retry_streak}/{retry_limit}）"
    else:
        prefix = "本輪掃描失敗，將於下輪重試"
    detail = format_failure_message_text(scan.error_message)
    return f"{prefix}：{detail}" if detail else prefix


def format_latest_error_indicator_label(
    scan: ScanRun | None,
    *,
    content_unavailable_current: bool | None = None,
    retrying_current: bool = False,
) -> str:
    """回傳 target header 使用的最近錯誤短標籤。"""

    if scan is None:
        return ""
    if retrying_current:
        return "將重試"
    current = (
        is_content_unavailable_scan(scan)
        if content_unavailable_current is None
        else content_unavailable_current
    )
    if current:
        return CONTENT_UNAVAILABLE_LABEL
    return "最近有錯誤"


def format_latest_error_indicator_title(
    scan: ScanRun | None,
    *,
    content_unavailable_current: bool | None = None,
    retrying_current: bool = False,
) -> str:
    """回傳 target header 最近錯誤的 hover 說明。"""

    if scan is None:
        return ""
    if retrying_current:
        return format_retrying_failure_title(scan)
    current = (
        is_content_unavailable_scan(scan)
        if content_unavailable_current is None
        else content_unavailable_current
    )
    if current:
        return CONTENT_UNAVAILABLE_TITLE
    return format_failure_message_text(scan.error_message)


def format_runtime_error_message(value: str) -> str:
    """把 runtime error 轉成使用者可讀訊息。"""

    if is_content_unavailable_runtime_error(value):
        return CONTENT_UNAVAILABLE_ERROR_MESSAGE
    return format_failure_message_text(value)


def format_latest_failed_scan_summary(
    scan: ScanRun | None,
    *,
    content_unavailable_current: bool = False,
) -> str:
    """回傳最近失敗掃描摘要。"""

    if not scan:
        return ""
    if is_content_unavailable_scan(scan) and content_unavailable_current:
        return CONTENT_UNAVAILABLE_LABEL
    if is_content_unavailable_scan(scan):
        return (
            f"{format_datetime_for_ui(scan.finished_at)} · "
            f"{CONTENT_UNAVAILABLE_HISTORY_MESSAGE}"
        )
    return (
        f"{format_datetime_for_ui(scan.finished_at)} · "
        f"{format_failure_message_text(scan.error_message)}"
    )
