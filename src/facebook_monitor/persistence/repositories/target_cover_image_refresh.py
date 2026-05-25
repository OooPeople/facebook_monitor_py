"""Target cover image refresh repository.

職責：保存 dashboard 壞圖上報觸發的 image-only metadata maintenance 狀態，
讓排程、節流與 worker 消化共用同一個持久來源。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class TargetCoverImageRefreshRepository:
    """保存 target cover image URL 背景刷新狀態。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, target_id: str) -> TargetCoverImageRefreshState | None:
        """依 target id 讀取 cover image refresh state。"""

        row = self.connection.execute(
            "SELECT * FROM target_cover_image_refresh_state WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        return _cover_refresh_state_from_row(row) if row else None

    def request_refresh(
        self,
        *,
        target_id: str,
        reported_url: str,
        min_interval_seconds: int,
        requested_at: datetime | None = None,
    ) -> CoverImageRefreshRequestStatus:
        """排程 image-only cover refresh；回傳 queued/pending/throttled。"""

        normalized_url = reported_url.strip()
        now = requested_at or utc_now()
        state = self.get(target_id)
        if state is not None and state.last_reported_url == normalized_url:
            if state.status == TargetCoverImageRefreshStatus.PENDING:
                return CoverImageRefreshRequestStatus.PENDING
            if _within_interval(
                state.requested_at,
                now=now,
                min_interval_seconds=min_interval_seconds,
            ):
                return CoverImageRefreshRequestStatus.THROTTLED
        self.connection.execute(
            """
            INSERT INTO target_cover_image_refresh_state (
                target_id, status, requested_at, last_attempted_at,
                last_succeeded_at, last_failed_at, last_reported_url,
                last_resolved_url, last_result, changed, error, updated_at
            )
            VALUES (?, ?, ?, '', '', '', ?, '', ?, 0, '', ?)
            ON CONFLICT(target_id) DO UPDATE SET
                status=excluded.status,
                requested_at=excluded.requested_at,
                last_reported_url=excluded.last_reported_url,
                last_resolved_url='',
                last_result=excluded.last_result,
                changed=0,
                error='',
                updated_at=excluded.updated_at
            """,
            (
                target_id,
                TargetCoverImageRefreshStatus.PENDING.value,
                encode_datetime(now),
                normalized_url,
                TargetCoverImageRefreshResult.QUEUED.value,
                encode_datetime(now),
            ),
        )
        return CoverImageRefreshRequestStatus.QUEUED

    def list_pending(self, *, limit: int) -> list[TargetCoverImageRefreshState]:
        """列出等待 worker 消化的 cover image refresh jobs。"""

        normalized_limit = max(int(limit), 0)
        if normalized_limit <= 0:
            return []
        rows = self.connection.execute(
            """
            SELECT * FROM target_cover_image_refresh_state
            WHERE status = ?
            ORDER BY requested_at, updated_at
            LIMIT ?
            """,
            (TargetCoverImageRefreshStatus.PENDING.value, normalized_limit),
        ).fetchall()
        return [_cover_refresh_state_from_row(row) for row in rows]

    def mark_attempted(
        self,
        target_id: str,
        *,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
        attempted_at: datetime | None = None,
    ) -> bool:
        """記錄 worker 已開始處理 cover image refresh。"""

        now = attempted_at or utc_now()
        where_sql, where_params = _target_pending_token_where(
            target_id,
            reported_url=reported_url,
            requested_at=requested_at,
        )
        cursor = self.connection.execute(
            f"""
            UPDATE target_cover_image_refresh_state
            SET last_attempted_at = ?,
                last_result = ?,
                updated_at = ?
            WHERE {where_sql}
            """,
            (
                encode_datetime(now),
                TargetCoverImageRefreshResult.ATTEMPTED.value,
                encode_datetime(now),
                *where_params,
            ),
        )
        return cursor.rowcount == 1

    def mark_succeeded(
        self,
        target_id: str,
        *,
        resolved_url: str,
        changed: bool,
        result: TargetCoverImageRefreshResult | None = None,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
        succeeded_at: datetime | None = None,
    ) -> bool:
        """標記 cover image refresh 成功並回到 idle。"""

        now = succeeded_at or utc_now()
        normalized_result = result or (
            TargetCoverImageRefreshResult.SUCCEEDED_CHANGED
            if changed
            else TargetCoverImageRefreshResult.SUCCEEDED_UNCHANGED
        )
        where_sql, where_params = _target_pending_token_where(
            target_id,
            reported_url=reported_url,
            requested_at=requested_at,
        )
        cursor = self.connection.execute(
            f"""
            UPDATE target_cover_image_refresh_state
            SET status = ?,
                last_succeeded_at = ?,
                last_resolved_url = ?,
                last_result = ?,
                changed = ?,
                error = '',
                updated_at = ?
            WHERE {where_sql}
            """,
            (
                TargetCoverImageRefreshStatus.IDLE.value,
                encode_datetime(now),
                resolved_url.strip(),
                normalized_result.value,
                1 if changed else 0,
                encode_datetime(now),
                *where_params,
            ),
        )
        return cursor.rowcount == 1

    def mark_stale_skipped(
        self,
        target_id: str,
        *,
        current_url: str,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
        skipped_at: datetime | None = None,
    ) -> bool:
        """現行 target 圖片 URL 已變更時，清掉過期的 pending refresh job。"""

        now = skipped_at or utc_now()
        where_sql, where_params = _target_pending_token_where(
            target_id,
            reported_url=reported_url,
            requested_at=requested_at,
        )
        cursor = self.connection.execute(
            f"""
            UPDATE target_cover_image_refresh_state
            SET status = ?,
                last_resolved_url = ?,
                last_result = ?,
                changed = 0,
                error = '',
                updated_at = ?
            WHERE {where_sql}
            """,
            (
                TargetCoverImageRefreshStatus.IDLE.value,
                current_url.strip(),
                TargetCoverImageRefreshResult.STALE_SKIPPED.value,
                encode_datetime(now),
                *where_params,
            ),
        )
        return cursor.rowcount == 1

    def mark_failed(
        self,
        target_id: str,
        error: str,
        *,
        result: TargetCoverImageRefreshResult = TargetCoverImageRefreshResult.FAILED,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
        failed_at: datetime | None = None,
    ) -> bool:
        """標記 cover image refresh 失敗；不清除既有 target 圖片 URL。"""

        now = failed_at or utc_now()
        where_sql, where_params = _target_pending_token_where(
            target_id,
            reported_url=reported_url,
            requested_at=requested_at,
        )
        cursor = self.connection.execute(
            f"""
            UPDATE target_cover_image_refresh_state
            SET status = ?,
                last_failed_at = ?,
                last_result = ?,
                error = ?,
                updated_at = ?
            WHERE {where_sql}
            """,
            (
                TargetCoverImageRefreshStatus.FAILED.value,
                encode_datetime(now),
                result.value,
                format_failure_message_text(error)[:500],
                encode_datetime(now),
                *where_params,
            ),
        )
        return cursor.rowcount == 1


def _target_pending_token_where(
    target_id: str,
    *,
    reported_url: str | None,
    requested_at: datetime | None,
) -> tuple[str, tuple[str, ...]]:
    """建立 pending cover refresh token guard，避免舊 worker 清掉新 request。"""

    clauses = ["target_id = ?", "status = ?"]
    params: list[str] = [target_id, TargetCoverImageRefreshStatus.PENDING.value]
    if reported_url is not None:
        clauses.append("last_reported_url = ?")
        params.append(reported_url.strip())
    if requested_at is not None:
        clauses.append("requested_at = ?")
        params.append(encode_datetime(requested_at))
    return " AND ".join(clauses), tuple(params)


def _within_interval(
    timestamp: datetime | None,
    *,
    now: datetime,
    min_interval_seconds: int,
) -> bool:
    """判斷同一 URL 是否仍在壞圖上報節流區間內。"""

    if timestamp is None:
        return False
    return (now - timestamp).total_seconds() < max(min_interval_seconds, 0)


def _cover_refresh_state_from_row(row: sqlite3.Row) -> TargetCoverImageRefreshState:
    """將 SQLite row 轉成 TargetCoverImageRefreshState。"""

    updated_at = decode_datetime(row["updated_at"])
    if updated_at is None:
        raise ValueError("cover image refresh row has invalid updated_at")
    return TargetCoverImageRefreshState(
        target_id=row["target_id"],
        status=TargetCoverImageRefreshStatus(row["status"]),
        requested_at=decode_datetime(row["requested_at"]),
        last_attempted_at=decode_datetime(row["last_attempted_at"]),
        last_succeeded_at=decode_datetime(row["last_succeeded_at"]),
        last_failed_at=decode_datetime(row["last_failed_at"]),
        last_reported_url=row["last_reported_url"],
        last_resolved_url=row["last_resolved_url"],
        last_result=TargetCoverImageRefreshResult(row["last_result"]),
        changed=bool(row["changed"]),
        error=row["error"],
        updated_at=updated_at,
    )
