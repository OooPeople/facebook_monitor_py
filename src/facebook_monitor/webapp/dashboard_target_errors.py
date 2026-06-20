"""TargetRow runtime 與最近錯誤 presenter helper。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.user_messages import format_runtime_skip_message
from facebook_monitor.webapp.dashboard_presenters import (
    format_latest_error_indicator_label,
)
from facebook_monitor.webapp.dashboard_presenters import (
    format_latest_error_indicator_title,
)
from facebook_monitor.webapp.dashboard_presenters import format_runtime_error_message
from facebook_monitor.webapp.dashboard_presenters import is_content_unavailable_runtime_error
from facebook_monitor.webapp.dashboard_presenters import is_content_unavailable_scan
from facebook_monitor.webapp.dashboard_presenters import is_retrying_failure_scan
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui


def runtime_error(row: Any) -> str:
    """回傳 runtime error 顯示文字。"""

    if row.runtime_state.runtime_status != TargetRuntimeStatus.ERROR:
        return ""
    return format_runtime_error_message(row.runtime_state.last_error)


def runtime_skip_reason(row: Any) -> str:
    """回傳最近一次 scan guard skip 原因。"""

    return format_runtime_skip_message(row.runtime_state.last_skip_reason)


def latest_error_label(row: Any) -> str:
    """回傳最近錯誤時間。"""

    if not row.latest_failed_scan_run:
        return ""
    return format_datetime_for_ui(row.latest_failed_scan_run.finished_at)


def latest_failed_scan_summary(row: Any) -> str:
    """回傳最近失敗掃描摘要。"""

    return row.card_summary_presenter.latest_failed_scan_summary


def latest_error_indicator_label(row: Any) -> str:
    """回傳 target header 的最近錯誤短標籤。"""

    return format_latest_error_indicator_label(
        row.latest_failed_scan_run,
        content_unavailable_current=row.content_unavailable_current,
        retrying_current=row.retrying_failure_current,
    )


def latest_error_indicator_title(row: Any) -> str:
    """回傳 target header 最近錯誤說明。"""

    return format_latest_error_indicator_title(
        row.latest_failed_scan_run,
        content_unavailable_current=row.content_unavailable_current,
        retrying_current=row.retrying_failure_current,
    )


def latest_error_indicator_kind(row: Any) -> str:
    """回傳最近錯誤 UI 類型。"""

    if row.content_unavailable_current:
        return "content-unavailable"
    if row.retrying_failure_current:
        return "retrying"
    return "error" if row.latest_failed_scan_run else ""


def retrying_failure_current(row: Any) -> bool:
    """回傳最近 failed scan 是否仍代表等待下輪重試的目前狀態。"""

    failed_scan = row.latest_failed_scan_run
    if not is_retrying_failure_scan(failed_scan):
        return False
    latest_scan = row.latest_scan_run
    if latest_scan is None:
        return True
    if failed_scan is None:
        return False
    return failed_scan.finished_at >= latest_scan.finished_at


def content_unavailable_current(row: Any) -> bool:
    """回傳連結失效是否仍代表目前狀態。"""

    failed_scan = row.latest_failed_scan_run
    if not is_content_unavailable_scan(failed_scan):
        return False
    if (
        row.runtime_state.runtime_status == TargetRuntimeStatus.ERROR
        and is_content_unavailable_runtime_error(row.runtime_state.last_error)
    ):
        return True
    latest_scan = row.latest_scan_run
    if latest_scan is None:
        return True
    if failed_scan is None:
        return False
    return failed_scan.finished_at >= latest_scan.finished_at


__all__ = [
    "content_unavailable_current",
    "latest_error_indicator_kind",
    "latest_error_indicator_label",
    "latest_error_indicator_title",
    "latest_error_label",
    "latest_failed_scan_summary",
    "retrying_failure_current",
    "runtime_error",
    "runtime_skip_reason",
]
