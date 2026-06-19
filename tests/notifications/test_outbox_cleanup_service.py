"""Notification outbox cleanup service tests。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.outbox_cleanup_service import (
    clear_failed_notification_outbox,
)
from facebook_monitor.notifications.outbox_cleanup_service import (
    clear_failed_notification_outbox_for_db,
)
from facebook_monitor.notifications.outbox_cleanup_service import (
    clear_target_notification_outbox,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.sqlite_connection import SqliteConnection

from tests.persistence.sqlite_test_helpers import notification_outbox_repository


def test_clear_failed_notification_outbox_for_db_preserves_live_rows(
    tmp_path: Path,
) -> None:
    """Settings cleanup 只清 failed rows，不清 pending / processing rows。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        TargetRepository(connection).save(target)
        target_id = target.id
        repo = notification_outbox_repository(connection)
        repo.enqueue(_outbox_entry(target_id=target_id, key="pending"))
        repo.enqueue(
            _outbox_entry(
                target_id=target_id,
                key="processing",
                status=NotificationOutboxStatus.PROCESSING_PENDING,
            )
        )
        repo.enqueue(
            _outbox_entry(
                target_id=target_id,
                key="failed",
                status=NotificationOutboxStatus.FAILED,
            )
        )
        connection.commit()

    assert clear_failed_notification_outbox_for_db(db_path=db_path) == 1

    with SqliteConnection(db_path) as sqlite:
        repo = notification_outbox_repository(sqlite.require_connection())
        assert repo.get_by_idempotency_key(f"{target_id}:pending:ntfy") is not None
        assert repo.get_by_idempotency_key(f"{target_id}:processing:ntfy") is not None
        assert repo.get_by_idempotency_key(f"{target_id}:failed:ntfy") is None


def test_clear_target_notification_outbox_preserves_other_targets(
    tmp_path: Path,
) -> None:
    """Target reset cleanup 只清指定 target 的 outbox rows。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        first = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        second = TargetDescriptor.for_group_posts(
            group_id="222",
            canonical_url="https://www.facebook.com/groups/222",
        )
        TargetRepository(connection).save(first)
        TargetRepository(connection).save(second)
        repo = notification_outbox_repository(connection)
        repo.enqueue(_outbox_entry(target_id=first.id, key="item"))
        repo.enqueue(_outbox_entry(target_id=second.id, key="item"))

        assert (
            clear_target_notification_outbox(
                notification_outbox=repo,
                target_id=first.id,
            )
            == 1
        )
        assert clear_failed_notification_outbox(
            app=cast(ApplicationContext, _AppWithOutbox(repo))
        ) == 0

        assert repo.get_by_idempotency_key(f"{first.id}:item:ntfy") is None
        assert repo.get_by_idempotency_key(f"{second.id}:item:ntfy") is not None


class _Repositories:
    """提供 cleanup service 測試需要的 repositories shape。"""

    def __init__(self, notification_outbox: object) -> None:
        self.notification_outbox = notification_outbox


class _AppWithOutbox:
    """提供 cleanup service 測試需要的 app shape。"""

    def __init__(self, notification_outbox: object) -> None:
        self.repositories = _Repositories(notification_outbox)


def _outbox_entry(
    *,
    target_id: str,
    key: str,
    status: NotificationOutboxStatus = NotificationOutboxStatus.PENDING,
) -> NotificationOutboxEntry:
    """建立 cleanup 測試用 outbox row。"""

    return NotificationOutboxEntry(
        idempotency_key=f"{target_id}:{key}:ntfy",
        target_id=target_id,
        item_key=key,
        item_kind=ItemKind.POST,
        channel=NotificationChannel.NTFY,
        title="title",
        message="message",
        status=status,
    )
