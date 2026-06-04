"""通知 outbox 管理 use case。

職責：提供 settings 頁需要的失敗通知摘要與明確清除入口，不改變一般
scan commit 後自動 dispatch pending 的主流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.user_messages import format_notification_event_message


@dataclass(frozen=True)
class NotificationOutboxHealth:
    """保存 settings 頁顯示的通知 outbox 健康摘要。"""

    pending_count: int
    processing_count: int
    failed_count: int
    terminal_count: int
    max_attempts: int
    last_failure_channel: str = ""
    last_failure_reason: str = ""

    @property
    def has_failed(self) -> bool:
        """回傳目前是否有 failed outbox rows。"""

        return self.failed_count > 0

    @property
    def summary_label(self) -> str:
        """回傳 settings 頁低干擾摘要文字。"""

        return (
            f"待送 {self.pending_count}，處理中 {self.processing_count}，"
            f"失敗 {self.failed_count}，已結束 {self.terminal_count}"
        )


def load_notification_outbox_health(db_path: Path) -> NotificationOutboxHealth:
    """讀取全域通知 outbox 健康摘要。"""

    with SqliteApplicationContext(db_path) as app_context:
        summary = app_context.repositories.notification_outbox.summarize_all()
        latest_failed = app_context.repositories.notification_outbox.latest_failed()
    return NotificationOutboxHealth(
        pending_count=summary.pending_count,
        processing_count=summary.processing_count,
        failed_count=summary.failed_count,
        terminal_count=summary.terminal_count,
        max_attempts=summary.max_attempts,
        last_failure_channel=latest_failed.channel.value if latest_failed else "",
        last_failure_reason=format_notification_event_message(latest_failed.last_error)
        if latest_failed
        else "",
    )


def clear_failed_notifications(*, db_path: Path) -> int:
    """清除 failed outbox rows，讓使用者明確結束不再重試的通知。"""

    with SqliteApplicationContext(db_path) as app_context:
        cleared_count = app_context.repositories.notification_outbox.clear_failed()
        app_context.repositories.notification_outbox.connection.commit()
        return cleared_count
