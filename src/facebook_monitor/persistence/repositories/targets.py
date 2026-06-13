"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.persistence.row_mappers import target_from_row
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class TargetRepository:
    """保存與查詢 target descriptor。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, target: TargetDescriptor) -> None:
        """新增或更新 target。"""

        self.connection.execute(
            """
            INSERT INTO targets (
                id, name, target_kind, group_id, group_name, parent_post_id,
                scope_id, canonical_url, group_cover_image_url, metadata_status,
                metadata_error, enabled, paused, worker_mode, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                target_kind=excluded.target_kind,
                group_id=excluded.group_id,
                group_name=excluded.group_name,
                parent_post_id=excluded.parent_post_id,
                scope_id=excluded.scope_id,
                canonical_url=excluded.canonical_url,
                group_cover_image_url=excluded.group_cover_image_url,
                metadata_status=excluded.metadata_status,
                metadata_error=excluded.metadata_error,
                enabled=excluded.enabled,
                paused=excluded.paused,
                worker_mode=excluded.worker_mode,
                updated_at=excluded.updated_at
            """,
            (
                target.id,
                target.name,
                target.target_kind.value,
                target.group_id,
                target.group_name,
                target.parent_post_id,
                target.scope_id,
                target.canonical_url,
                target.group_cover_image_url,
                target.metadata_status.value,
                target.metadata_error,
                int(target.enabled),
                int(target.paused),
                target.worker_mode.value,
                encode_datetime(target.created_at),
                encode_datetime(target.updated_at),
            ),
        )

    def get(self, target_id: str) -> TargetDescriptor | None:
        """依 id 查詢 target。"""

        row = self.connection.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        return target_from_row(row) if row else None

    def delete(self, target_id: str) -> bool:
        """刪除單一 target，回傳是否真的刪到資料。"""

        cursor = self.connection.execute("DELETE FROM targets WHERE id = ?", (target_id,))
        return cursor.rowcount > 0

    def list_enabled(self) -> list[TargetDescriptor]:
        """列出啟用且未暫停的 target。"""

        rows = self.connection.execute(
            "SELECT * FROM targets WHERE enabled = 1 AND paused = 0 ORDER BY created_at"
        ).fetchall()
        return [target_from_row(row) for row in rows]

    def list_all(self) -> list[TargetDescriptor]:
        """列出所有 target，供設定管理入口使用。"""

        rows = self.connection.execute("SELECT * FROM targets ORDER BY created_at").fetchall()
        return [target_from_row(row) for row in rows]

    def find_by_kind_scope(
        self,
        target_kind: TargetKind,
        scope_id: str,
    ) -> TargetDescriptor | None:
        """依 target 類型與 scope 查詢既有 target。"""

        row = self.connection.execute(
            """
            SELECT * FROM targets
            WHERE target_kind = ? AND scope_id = ?
            ORDER BY created_at
            LIMIT 1
            """,
            (target_kind.value, scope_id),
        ).fetchone()
        return target_from_row(row) if row else None

    def list_by_metadata_status(
        self,
        status: TargetMetadataStatus,
        *,
        limit: int,
        exclude_ids: tuple[str, ...] = (),
    ) -> list[TargetDescriptor]:
        """列出指定 metadata 狀態的 target，供 resident worker 消化 pending job。"""

        normalized_limit = max(int(limit), 0)
        if normalized_limit <= 0:
            return []
        exclude_clause, exclude_params = _exclude_ids_clause(exclude_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM targets
            WHERE metadata_status = ?
            {exclude_clause}
            ORDER BY updated_at, created_at
            LIMIT ?
            """,
            (
                status.value,
                *exclude_params,
                normalized_limit,
            ),
        ).fetchall()
        return [target_from_row(row) for row in rows]

    def list_polluted_group_name_candidates(
        self,
        *,
        limit: int,
        retry_failed_before: datetime,
        exclude_ids: tuple[str, ...] = (),
    ) -> list[TargetDescriptor]:
        """列出名稱 metadata 已知遭 Facebook 錯誤頁污染的修復候選。"""

        normalized_limit = max(int(limit), 0)
        if normalized_limit <= 0:
            return []
        exclude_clause, exclude_params = _exclude_ids_clause(exclude_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM targets
            WHERE (lower(name) = ? OR lower(group_name) = ?)
              AND metadata_status != ?
              AND (metadata_status != ? OR updated_at <= ?)
              {exclude_clause}
            ORDER BY updated_at, created_at
            LIMIT ?
            """,
            (
                "facebook | error",
                "facebook | error",
                TargetMetadataStatus.PENDING.value,
                TargetMetadataStatus.FAILED.value,
                encode_datetime(retry_failed_before),
                *exclude_params,
                normalized_limit,
            ),
        ).fetchall()
        return [target_from_row(row) for row in rows]

    def list_polluted_group_cover_image_candidates(
        self,
        *,
        limit: int,
        exclude_ids: tuple[str, ...] = (),
    ) -> list[TargetDescriptor]:
        """列出只有封面圖為 Facebook 通用圖的 image-only refresh 候選。"""

        normalized_limit = max(int(limit), 0)
        if normalized_limit <= 0:
            return []
        exclude_clause, exclude_params = _exclude_ids_clause(exclude_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM targets
            WHERE (
                lower(group_cover_image_url) LIKE ?
                OR lower(group_cover_image_url) LIKE ?
                OR lower(group_cover_image_url) LIKE ?
            )
              AND lower(name) != ?
              AND lower(group_name) != ?
              AND metadata_status != ?
              {exclude_clause}
            ORDER BY updated_at, created_at
            LIMIT ?
            """,
            (
                "https://static.facebook.com/images/logos/%",
                "https://facebook.com/images/logos/%",
                "https://%.facebook.com/images/logos/%",
                "facebook | error",
                "facebook | error",
                TargetMetadataStatus.PENDING.value,
                *exclude_params,
                normalized_limit,
            ),
        ).fetchall()
        return [target_from_row(row) for row in rows]


def _exclude_ids_clause(exclude_ids: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """建立 target id 排除條件，避免 candidate 來源重複。"""

    normalized_ids = tuple(str(target_id or "").strip() for target_id in exclude_ids)
    normalized_ids = tuple(target_id for target_id in normalized_ids if target_id)
    if not normalized_ids:
        return "", ()
    placeholders = ",".join("?" for _ in normalized_ids)
    return f"AND id NOT IN ({placeholders})", normalized_ids

