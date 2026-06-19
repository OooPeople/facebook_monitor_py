"""Notification outbox cleanup service。

職責：集中命名 notification outbox cleanup use cases。這層只包現有
repository/maintenance policy，不新增 retry、dead-letter 或 pending row 清理語義。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from facebook_monitor.persistence.repositories.notification_outbox import (
    NotificationOutboxRepository,
)

if TYPE_CHECKING:
    from facebook_monitor.application.context import ApplicationContext


def clear_failed_notification_outbox(*, app: ApplicationContext) -> int:
    """清除 failed outbox rows；不清 pending / processing rows。"""

    return app.repositories.notification_outbox.clear_failed()


def clear_failed_notification_outbox_for_db(*, db_path: Path) -> int:
    """Settings 用 failed outbox cleanup 入口，使用獨立 application context。"""

    from facebook_monitor.application.context import SqliteApplicationContext

    with SqliteApplicationContext(db_path) as app_context:
        cleared_count = clear_failed_notification_outbox(app=app_context)
        app_context.repositories.notification_outbox.connection.commit()
        return cleared_count


def clear_target_notification_outbox(
    *,
    notification_outbox: NotificationOutboxRepository,
    target_id: str,
) -> int:
    """清除單一 target 的 outbox rows，供重置通知狀態使用。"""

    return notification_outbox.clear_by_target(target_id)


__all__ = [
    "clear_failed_notification_outbox",
    "clear_failed_notification_outbox_for_db",
    "clear_target_notification_outbox",
]
