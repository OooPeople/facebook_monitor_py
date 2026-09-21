"""集中測試專用的唯讀 repository 查詢。"""

from __future__ import annotations

from facebook_monitor.core.defaults import PYTHON_PERSISTENCE_QUERY_DEFAULTS
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.persistence.repositories.notification_events import (
    NotificationEventRepository,
)
from facebook_monitor.persistence.repositories.notification_outbox import (
    NotificationOutboxRepository,
)
from facebook_monitor.persistence.row_mappers import notification_event_from_row


def list_notification_events_by_target(
    repository: NotificationEventRepository,
    target_id: str,
    limit: int = PYTHON_PERSISTENCE_QUERY_DEFAULTS.list_limit,
) -> list[NotificationEvent]:
    """依 target id 唯讀查詢最近 notification events。"""

    rows = repository.connection.execute(
        """
        SELECT * FROM notification_events
        WHERE target_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (target_id, limit),
    ).fetchall()
    return [notification_event_from_row(row) for row in rows]


def latest_notification_event_by_target(
    repository: NotificationEventRepository,
    target_id: str,
) -> NotificationEvent | None:
    """唯讀查詢單一 target 最近一筆 notification event。"""

    events = list_notification_events_by_target(repository, target_id, limit=1)
    return events[0] if events else None


def list_pending_notification_outbox(
    repository: NotificationOutboxRepository,
    limit: int = PYTHON_PERSISTENCE_QUERY_DEFAULTS.list_limit,
) -> list[NotificationOutboxEntry]:
    """唯讀列出 pending outbox，不執行 claim 或改變狀態。"""

    rows = repository.connection.execute(
        """
        SELECT idempotency_key FROM notification_outbox
        WHERE status = ?
        ORDER BY id
        LIMIT ?
        """,
        (NotificationOutboxStatus.PENDING.value, limit),
    ).fetchall()
    entries: list[NotificationOutboxEntry] = []
    for row in rows:
        entry = repository.get_by_idempotency_key(str(row["idempotency_key"]))
        if entry is None:
            raise AssertionError("pending notification outbox row disappeared during test read")
        entries.append(entry)
    return entries
