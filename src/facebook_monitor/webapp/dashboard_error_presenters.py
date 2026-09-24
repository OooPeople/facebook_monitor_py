"""Dashboard target error presentation helpers。"""

from __future__ import annotations

from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import split_coded_message
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui


CONTENT_UNAVAILABLE_LABEL = "內容無法查看"
LEGACY_CONTENT_UNAVAILABLE_LABEL = "連結已失效"
CONTENT_UNAVAILABLE_TITLE = "Facebook 顯示目前無法查看此內容，監視已停止。"
CONTENT_UNAVAILABLE_CONFIRMED_TITLE = (
    "Facebook 連續三次顯示目前無法查看此內容，監視已停止。"
)
CONTENT_UNAVAILABLE_ERROR_MESSAGE = (
    "內容無法查看：Facebook 顯示目前無法查看此內容，監視已停止。"
)
CONTENT_UNAVAILABLE_CONFIRMED_ERROR_MESSAGE = (
    "內容持續無法查看：Facebook 在連續三次頁面確認中都顯示目前無法查看此內容，監視已停止。"
)
CONTENT_UNAVAILABLE_HISTORY_MESSAGE = (
    "曾偵測到 Facebook 內容無法查看；後續掃描已恢復。"
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
        or scan.error_message.startswith(f"{LEGACY_CONTENT_UNAVAILABLE_LABEL}：")
    )


def is_content_unavailable_runtime_error(value: str) -> bool:
    """判斷 runtime error 是否代表 Facebook 內容不可見。"""

    code, _detail = split_coded_message(value)
    return code == CONTENT_UNAVAILABLE_REASON or value.startswith(
        (f"{CONTENT_UNAVAILABLE_LABEL}：", f"{LEGACY_CONTENT_UNAVAILABLE_LABEL}：")
    )


def is_retrying_failure_scan(scan: ScanRun | None) -> bool:
    """判斷 failed scan 是否為未達上限、將於下輪重試的失敗。"""

    if scan is None:
        return False
    metadata = scan.metadata or {}
    return bool(metadata.get("retryable")) and metadata.get("runtime_action") == "will_retry"


def is_confirmed_content_unavailable_scan(scan: ScanRun | None) -> bool:
    """判斷 failed scan 是否帶有新制三次確認完成的明確證據。"""

    if scan is None or not is_content_unavailable_scan(scan):
        return False
    metadata = scan.metadata or {}
    retry_streak = metadata.get("retry_streak")
    retry_limit = metadata.get("retry_limit")
    return retry_streak == 3 and retry_limit == 3


def format_retrying_failure_title(scan: ScanRun) -> str:
    """格式化可重試 failed scan 的 hover 說明。"""

    metadata = scan.metadata or {}
    retry_streak = metadata.get("retry_streak")
    retry_limit = metadata.get("retry_limit")
    retry_delay_seconds = metadata.get("retry_delay_seconds")
    if retry_streak and retry_limit:
        prefix = f"本輪掃描失敗，將於下輪重試（{retry_streak}/{retry_limit}）"
    else:
        prefix = "本輪掃描失敗，將於下輪重試"
    if is_content_unavailable_scan(scan):
        retry_text = (
            f"本次失敗後會等待 {retry_delay_seconds} 秒，再以新頁面重新確認。"
            if isinstance(retry_delay_seconds, int) and retry_delay_seconds > 0
            else "系統稍後會以新頁面重新確認。"
        )
        return f"{prefix}：Facebook 暫時顯示無法查看此內容，{retry_text}"
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
        if is_confirmed_content_unavailable_scan(scan):
            return CONTENT_UNAVAILABLE_CONFIRMED_TITLE
        return CONTENT_UNAVAILABLE_TITLE
    return format_failure_message_text(scan.error_message)


def format_runtime_error_message(value: str, scan: ScanRun | None = None) -> str:
    """把 runtime error 轉成使用者可讀訊息。"""

    if is_content_unavailable_runtime_error(value):
        if is_confirmed_content_unavailable_scan(scan):
            return CONTENT_UNAVAILABLE_CONFIRMED_ERROR_MESSAGE
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
