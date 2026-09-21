"""Facebook temporary-block singleton warning repository。"""

from __future__ import annotations

from datetime import datetime
import sqlite3

from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import (
    TemporaryBlockWarningSnapshot,
)
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class FacebookTemporaryBlockWarningRepository:
    """在呼叫端 transaction 內讀寫 singleton advisory warning。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> TemporaryBlockWarningSnapshot | None:
        """讀取最近一次 warning；不存在時不建立資料。"""

        row = self.connection.execute(
            "SELECT * FROM facebook_temporary_block_warning WHERE id = 1"
        ).fetchone()
        return _snapshot(row) if row is not None else None

    def record(
        self,
        *,
        detected_at: datetime,
        warning_until: datetime,
        source_kind: FacebookWorkSourceKind,
        operation_kind: FacebookProductOperationKind,
        action_kind: FacebookActionKind,
        updated_at: datetime,
    ) -> TemporaryBlockWarningSnapshot:
        """推進 generation 並保存最近一次 confirmed block。"""

        self.connection.execute(
            """
            INSERT INTO facebook_temporary_block_warning (
                id, generation, detected_at, warning_until,
                source_kind, operation_kind, action_kind, updated_at
            )
            VALUES (1, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                generation = facebook_temporary_block_warning.generation + 1,
                detected_at = excluded.detected_at,
                warning_until = excluded.warning_until,
                source_kind = excluded.source_kind,
                operation_kind = excluded.operation_kind,
                action_kind = excluded.action_kind,
                updated_at = excluded.updated_at
            """,
            (
                encode_datetime(detected_at),
                encode_datetime(warning_until),
                source_kind.value,
                operation_kind.value,
                action_kind.value,
                encode_datetime(updated_at),
            ),
        )
        snapshot = self.get()
        if snapshot is None:
            raise RuntimeError("temporary block warning write did not produce a row")
        return snapshot


def _snapshot(row: sqlite3.Row) -> TemporaryBlockWarningSnapshot:
    """將 SQLite row 轉成 typed warning snapshot。"""

    detected_at = decode_datetime(str(row["detected_at"]))
    warning_until = decode_datetime(str(row["warning_until"]))
    updated_at = decode_datetime(str(row["updated_at"]))
    if detected_at is None or warning_until is None or updated_at is None:
        raise ValueError("temporary block warning timestamps are required")
    return TemporaryBlockWarningSnapshot(
        generation=int(row["generation"]),
        detected_at=detected_at,
        warning_until=warning_until,
        source_kind=FacebookWorkSourceKind(str(row["source_kind"])),
        operation_kind=FacebookProductOperationKind(str(row["operation_kind"])),
        action_kind=FacebookActionKind(str(row["action_kind"])),
        updated_at=updated_at,
    )


__all__ = ["FacebookTemporaryBlockWarningRepository"]
