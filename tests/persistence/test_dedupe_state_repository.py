"""Target dedupe epoch repository tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any
from typing import cast

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.repositories.dedupe_state import DedupeStateRepository
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema


class _InterleavingConnection:
    """在第一次建立 epoch row 前，讓另一個 connection 先 advance。"""

    def __init__(self, connection: Any, before_first_insert: Any) -> None:
        self._connection = connection
        self._before_first_insert = before_first_insert
        self._triggered = False

    def execute(self, sql: str, parameters: object = ()) -> Any:
        """攔截 target_dedupe_state insert，其餘 SQL 交給原 connection。"""

        if (
            not self._triggered
            and "INSERT INTO target_dedupe_state" in sql
        ):
            self._triggered = True
            self._before_first_insert()
        return self._connection.execute(sql, parameters)


def test_advance_epoch_is_atomic_when_row_is_created_concurrently(
    tmp_path: Path,
) -> None:
    """兩個 reset 同時遇到缺 row 時，dedupe epoch 不可遺失第二次 advance。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="target-1",
            canonical_url="https://www.facebook.com/groups/target-1",
        )
        TargetRepository(connection).save(target)
        connection.commit()
    target_id = target.id

    with SqliteConnection(db_path) as sqlite_a, SqliteConnection(db_path) as sqlite_b:
        connection_a = sqlite_a.require_connection()
        connection_b = sqlite_b.require_connection()
        initialize_schema(connection_a)
        initialize_schema(connection_b)

        def advance_from_other_connection() -> None:
            assert DedupeStateRepository(connection_b).advance_epoch(target_id) == 1
            connection_b.commit()

        interleaving_connection = _InterleavingConnection(
            connection_a,
            advance_from_other_connection,
        )
        next_epoch = DedupeStateRepository(
            cast(sqlite3.Connection, interleaving_connection)
        ).advance_epoch(target_id)
        connection_a.commit()

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        stored_epoch = DedupeStateRepository(connection).peek_current_epoch(target_id)

    assert next_epoch == 2
    assert stored_epoch == 2
