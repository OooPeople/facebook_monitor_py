"""Runtime failure notification message builders。"""

from __future__ import annotations

from facebook_monitor.application.target_display import format_target_display_name
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.user_messages import format_failure_reason


def build_runtime_failure_notification_message(
    *,
    target: TargetDescriptor,
    reason: str,
    failure_count: int,
    error_message: str,
    target_stopped: bool = True,
) -> tuple[str, str]:
    """建立 target runtime failure 通知標題與內容。"""

    target_name = format_target_display_name(target)
    reason_label = format_failure_reason(reason)
    count = max(int(failure_count), 1)
    title = "Facebook Monitor target error"
    final_line = (
        "系統已停止此監視項目，請開啟 Web UI 檢查。"
        if target_stopped
        else "系統已記錄背景掃描錯誤，請開啟 Web UI 檢查。"
    )
    message = "\n".join(
        (
            f"監視項目: {target_name}",
            f"錯誤類型: {reason_label}",
            f"連續次數: {count}",
            f"狀態: {error_message or reason_label}",
            final_line,
        )
    )
    return title, message
