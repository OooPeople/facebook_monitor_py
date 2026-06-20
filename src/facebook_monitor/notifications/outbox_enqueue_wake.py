"""Notification outbox enqueue 後的 dispatcher wake hook。"""

from __future__ import annotations

import logging

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.notifications.outbox_dispatcher import (
    wake_notification_outbox_dispatcher_for_db,
)


logger = logging.getLogger(__name__)


def queue_notification_outbox_dispatch_wake_after_commit(
    app: ApplicationContext,
) -> None:
    """註冊 commit 後喚醒 outbox dispatcher；未註冊時 pending rows 留在 DB。"""

    def wake_after_commit() -> None:
        if app.db_path is None:
            raise RuntimeError("notification outbox dispatch requires application db_path")
        if not wake_notification_outbox_dispatcher_for_db(app.db_path):
            logger.debug(
                "notification_outbox_dispatcher_not_registered db_path=%s",
                app.db_path,
            )

    app.run_after_commit_once(
        "notification_outbox_dispatch_wake",
        wake_after_commit,
    )
