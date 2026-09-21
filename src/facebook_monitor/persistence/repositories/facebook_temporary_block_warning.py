"""Facebook temporary-block singleton warning repository。"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from enum import StrEnum
import sqlite3
from typing import TypeVar

from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import (
    TemporaryBlockWarningSnapshot,
)
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime

_EnumValue = TypeVar("_EnumValue", bound=StrEnum)


class TemporaryBlockWarningDecodeError(ValueError):
    """Warning singleton row 違反 durable storage contract。"""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__("temporary block warning row is invalid")


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

    generation = _decode_generation(row["generation"])
    detected_at = _decode_required_utc_datetime(row["detected_at"], "detected_at")
    warning_until = _decode_required_utc_datetime(row["warning_until"], "warning_until")
    updated_at = _decode_required_utc_datetime(row["updated_at"], "updated_at")
    if warning_until <= detected_at:
        raise TemporaryBlockWarningDecodeError("warning_window")
    return TemporaryBlockWarningSnapshot(
        generation=generation,
        detected_at=detected_at,
        warning_until=warning_until,
        source_kind=_decode_enum(
            row["source_kind"],
            enum_type=FacebookWorkSourceKind,
            field="source_kind",
        ),
        operation_kind=_decode_enum(
            row["operation_kind"],
            enum_type=FacebookProductOperationKind,
            field="operation_kind",
        ),
        action_kind=_decode_enum(
            row["action_kind"],
            enum_type=FacebookActionKind,
            field="action_kind",
        ),
        updated_at=updated_at,
    )


def _decode_generation(raw_value: object) -> int:
    """Generation 必須維持 SQLite INTEGER storage class 與正整數範圍。"""

    if type(raw_value) is not int or raw_value < 1:
        raise TemporaryBlockWarningDecodeError("generation")
    return raw_value


def _decode_required_utc_datetime(raw_value: object, field: str) -> datetime:
    """解碼 required UTC datetime，錯誤不得洩漏原始 DB 值。"""

    if not isinstance(raw_value, str) or not raw_value:
        raise TemporaryBlockWarningDecodeError(field)
    try:
        value = decode_datetime(raw_value)
    except ValueError:
        raise TemporaryBlockWarningDecodeError(field) from None
    if value is None or value.utcoffset() != timedelta(0):
        raise TemporaryBlockWarningDecodeError(field)
    return value


def _decode_enum(
    raw_value: object,
    *,
    enum_type: type[_EnumValue],
    field: str,
) -> _EnumValue:
    """解碼 warning enum，錯誤只帶安全欄位名稱。"""

    if not isinstance(raw_value, str):
        raise TemporaryBlockWarningDecodeError(field)
    try:
        return enum_type(raw_value)
    except ValueError:
        raise TemporaryBlockWarningDecodeError(field) from None


__all__ = [
    "FacebookTemporaryBlockWarningRepository",
    "TemporaryBlockWarningDecodeError",
]
