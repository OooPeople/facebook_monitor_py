"""Managed profile identity durable binding repository。

職責：保存單一正式 managed profile 的隨機 marker UUID，讓檔案遺失或被替換時
不會靜默切換到新的 circuit / pacing scope。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sqlite3
from uuid import UUID

from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


@dataclass(frozen=True)
class ManagedProfileIdentityBinding:
    """保存不含 Facebook 帳號資料的 durable marker binding。"""

    marker_uuid: UUID
    bound_at: datetime
    updated_at: datetime


class ManagedProfileIdentityRepository:
    """讀寫 singleton managed profile identity binding。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> ManagedProfileIdentityBinding | None:
        """讀取既有 binding；資料損毀時 fail closed。"""

        row = self.connection.execute(
            """
            SELECT marker_uuid, bound_at, updated_at
            FROM managed_profile_identity_binding
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return None
        try:
            marker_uuid = UUID(str(row["marker_uuid"]))
            bound_at = decode_datetime(str(row["bound_at"]))
            updated_at = decode_datetime(str(row["updated_at"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("managed profile identity binding is invalid") from exc
        if bound_at is None or updated_at is None:
            raise ValueError("managed profile identity binding is invalid")
        return ManagedProfileIdentityBinding(
            marker_uuid=marker_uuid,
            bound_at=bound_at,
            updated_at=updated_at,
        )

    def bind_if_absent(
        self,
        marker_uuid: UUID,
        *,
        bound_at: datetime,
    ) -> ManagedProfileIdentityBinding:
        """首次以 INSERT OR IGNORE 綁定 UUID，並回讀唯一 truth。"""

        encoded_at = encode_datetime(bound_at)
        self.connection.execute(
            """
            INSERT OR IGNORE INTO managed_profile_identity_binding (
                id, marker_uuid, bound_at, updated_at
            ) VALUES (1, ?, ?, ?)
            """,
            (str(marker_uuid), encoded_at, encoded_at),
        )
        binding = self.get()
        if binding is None:
            raise RuntimeError("failed to bind managed profile identity")
        return binding

    def list_scoped_safety_evidence(self) -> tuple[str, ...]:
        """列出三類 durable safety state 使用過的 distinct profile scopes。"""

        rows = self.connection.execute(
            """
            SELECT profile_scope_key FROM facebook_access_circuit_state
            UNION
            SELECT profile_scope_key FROM facebook_automation_pacing_state
            UNION
            SELECT profile_scope_key FROM facebook_session_recovery_state
            ORDER BY profile_scope_key
            """
        ).fetchall()
        return tuple(str(row["profile_scope_key"]) for row in rows)


__all__ = [
    "ManagedProfileIdentityBinding",
    "ManagedProfileIdentityRepository",
]
