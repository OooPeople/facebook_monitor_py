"""Persistence smoke tests。"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any
from typing import cast

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.repositories.logical_items import LogicalItemRepository
from facebook_monitor.persistence.repositories.notification_dedupe import NotificationDedupeRepository
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema

from tests.persistence.sqlite_test_helpers import notification_outbox_repository


class _PrefetchedRowsCursor:
    """回傳已預先讀出的 rows，讓測試可插入跨 connection race。"""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        """回傳預先讀取的 rows。"""

        return self._rows


class _RaceLoserConnection:
    """在 claim 讀到 candidate 後，讓另一個 connection 先 claim 成功。"""

    def __init__(self, connection: Any, on_candidates_selected: Any) -> None:
        self._connection = connection
        self._on_candidates_selected = on_candidates_selected
        self._triggered = False

    def execute(self, sql: str, parameters: object = ()) -> Any:
        """攔截 candidate SELECT，其餘 SQL 交給原 connection。"""

        if (
            not self._triggered
            and "SELECT id FROM notification_outbox" in sql
            and "WHERE status = ?" in sql
        ):
            rows = self._connection.execute(sql, parameters).fetchall()
            self._triggered = True
            self._on_candidates_selected()
            return _PrefetchedRowsCursor(rows)
        return self._connection.execute(sql, parameters)

    def commit(self) -> None:
        """提交底層 SQLite transaction。"""

        self._connection.commit()


def test_notification_outbox_clear_by_target_only_deletes_target_rows(
    tmp_path: Path,
) -> None:
    """清除 target 通知紀錄會刪該 target 所有 outbox 狀態。"""

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
        for status in NotificationOutboxStatus:
            repo.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{first.id}:{status.value}:ntfy",
                    target_id=first.id,
                    item_key=status.value,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title=status.value,
                    message=status.value,
                    status=status,
                )
            )
        repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{second.id}:item-hash:ntfy",
                target_id=second.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        assert repo.clear_by_target(first.id) == len(NotificationOutboxStatus)

        for status in NotificationOutboxStatus:
            assert repo.get_by_idempotency_key(f"{first.id}:{status.value}:ntfy") is None
        assert repo.get_by_idempotency_key(f"{second.id}:item-hash:ntfy") is not None


def test_notification_dedupe_blocks_duplicate_after_terminal_outbox_pruned(
    tmp_path: Path,
) -> None:
    """terminal outbox 刪除後仍由 notification_dedupe 擋同 logical item/channel。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        logical_result = LogicalItemRepository(connection).mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="item-a",
                item_kind=ItemKind.POST,
            ),
            item_keys=("item-a", "item-a-alias"),
        )
        dedupe_repo = NotificationDedupeRepository(connection)
        first = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=logical_result.logical_item_id,
            item_key="item-a",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        outbox_repo = notification_outbox_repository(connection)
        outbox = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-a:ntfy",
                dedupe_id=first.dedupe_id,
                target_id=target.id,
                item_key="item-a",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        assert outbox.id is not None
        outbox_repo.mark_result(
            entry_id=outbox.id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
        )
        connection.execute("DELETE FROM notification_outbox WHERE id = ?", (outbox.id,))
        second = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=logical_result.logical_item_id,
            item_key="item-a-alias",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )

    assert first.created
    assert not second.created


def test_notification_dedupe_duplicate_match_preserves_failed_diagnostics(
    tmp_path: Path,
) -> None:
    """match dedupe duplicate 不應清空既有 failed ledger 診斷。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        logical_result = LogicalItemRepository(connection).mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="item-a",
                item_kind=ItemKind.POST,
            ),
            item_keys=("item-a",),
        )
        dedupe_repo = NotificationDedupeRepository(connection)
        first = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=logical_result.logical_item_id,
            item_key="item-a",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        outbox_repo = notification_outbox_repository(connection)
        outbox = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-a:ntfy",
                dedupe_id=first.dedupe_id,
                target_id=target.id,
                item_key="item-a",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        assert outbox.id is not None
        outbox_repo.mark_result(
            entry_id=outbox.id,
            status=NotificationOutboxStatus.FAILED,
            attempts=1,
            message="ntfy failed",
        )
        second = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=logical_result.logical_item_id,
            item_key="item-a",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        dedupe_row = connection.execute(
            """
            SELECT status, failure_reason, failure_count
            FROM notification_dedupe
            WHERE id = ?
            """,
            (first.dedupe_id,),
        ).fetchone()

    assert not second.created
    assert dedupe_row is not None
    assert dedupe_row["status"] == "failed"
    assert dedupe_row["failure_reason"] == "ntfy failed"
    assert dedupe_row["failure_count"] == 0


def test_notification_outbox_clear_failed_only_deletes_failed_rows(
    tmp_path: Path,
) -> None:
    """全域 failed 清除是破壞性操作，但不可誤刪其他 outbox 狀態。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        repo = notification_outbox_repository(connection)
        for status in NotificationOutboxStatus:
            repo.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:{status.value}:desktop",
                    target_id=target.id,
                    item_key=status.value,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.DESKTOP,
                    title=status.value,
                    message=status.value,
                    status=status,
                )
            )

        assert repo.clear_failed() == 1
        replacement = repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:failed:desktop",
                target_id=target.id,
                item_key="failed",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="replacement",
                message="replacement",
                status=NotificationOutboxStatus.PENDING,
            )
        )
        loaded_by_status = {
            status: repo.get_by_idempotency_key(f"{target.id}:{status.value}:desktop")
            for status in NotificationOutboxStatus
        }

    assert loaded_by_status[NotificationOutboxStatus.FAILED] is not None
    assert loaded_by_status[NotificationOutboxStatus.FAILED] == replacement
    for status in (
        NotificationOutboxStatus.PENDING,
        NotificationOutboxStatus.PROCESSING_PENDING,
        NotificationOutboxStatus.SENT,
        NotificationOutboxStatus.PROCESSING_FAILED,
        NotificationOutboxStatus.SKIPPED,
    ):
        loaded_entry = loaded_by_status[status]
        assert loaded_entry is not None
        assert loaded_entry.status == status


def test_notification_outbox_claim_pending_is_single_owner_across_connections(
    tmp_path: Path,
) -> None:
    """兩個 SQLite connection 不得 claim 到同一筆 pending outbox。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        notification_outbox_repository(connection).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:ntfy",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    with SqliteConnection(db_path) as sqlite_a, SqliteConnection(db_path) as sqlite_b:
        connection_a = sqlite_a.require_connection()
        connection_b = sqlite_b.require_connection()
        initialize_schema(connection_a)
        initialize_schema(connection_b)

        claimed_a = notification_outbox_repository(connection_a).claim_pending()
        claimed_b = notification_outbox_repository(connection_b).claim_pending()

        assert len(claimed_a) == 1
        assert claimed_a[0].status == NotificationOutboxStatus.PROCESSING_PENDING
        assert claimed_b == []

        notification_outbox_repository(connection_a).mark_result(
            entry_id=claimed_a[0].id or 0,
            status=NotificationOutboxStatus.SENT,
            attempts=claimed_a[0].attempts + 1,
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = notification_outbox_repository(connection)
        loaded = repo.get_by_idempotency_key(f"{target.id}:item-hash:ntfy")

    assert loaded is not None
    assert loaded.status == NotificationOutboxStatus.SENT
    assert loaded.attempts == 1


def test_notification_outbox_claim_race_loser_closes_transaction(
    tmp_path: Path,
) -> None:
    """candidate 被其他 connection 先 claim 時，loser 不應留下 open transaction。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        notification_outbox_repository(connection).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-race:ntfy",
                target_id=target.id,
                item_key="item-race",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    with SqliteConnection(db_path) as sqlite_a, SqliteConnection(db_path) as sqlite_b:
        connection_a = sqlite_a.require_connection()
        connection_b = sqlite_b.require_connection()
        initialize_schema(connection_a)
        initialize_schema(connection_b)

        def claim_from_other_connection() -> None:
            claimed = notification_outbox_repository(connection_b).claim_pending()
            assert len(claimed) == 1

        race_connection = _RaceLoserConnection(
            connection_a,
            claim_from_other_connection,
        )
        claimed = notification_outbox_repository(
            cast(sqlite3.Connection, race_connection)
        ).claim_pending()

        assert claimed == []
        assert not connection_a.in_transaction


def test_notification_outbox_recover_stale_processing_skips_write_without_candidates(
    tmp_path: Path,
) -> None:
    """沒有過期 processing rows 時，recovery 不應開啟 SQLite write transaction。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.commit()
        repo = notification_outbox_repository(connection)

        recovered_count = repo.recover_stale_processing(older_than_seconds=60)

        assert recovered_count == 0
        assert not connection.in_transaction


def test_notification_outbox_recovers_stale_processing_for_future_claim(
    tmp_path: Path,
) -> None:
    """過期 pending processing outbox 可回收成 pending，避免 crash 後永久卡住。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        repo = notification_outbox_repository(connection)
        repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:stale:ntfy",
                target_id=target.id,
                item_key="stale",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        claimed = repo.claim_pending()
        assert len(claimed) == 1
        connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (claimed[0].id,),
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = notification_outbox_repository(connection)

        recovered_count = repo.recover_stale_processing(older_than_seconds=60)
        claimed_again = repo.claim_pending()

    assert recovered_count == 1
    assert len(claimed_again) == 1
    assert claimed_again[0].status == NotificationOutboxStatus.PROCESSING_PENDING
