"""Hit-record read-side queries."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.hit_record_models import FullHitRecordRow
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.read_model_context import read_application_context
from facebook_monitor.webapp.read_model_context import (
    raise_dashboard_read_unavailable_if_locked,
)
from facebook_monitor.webapp.read_model_invariants import has_target_or_runtime_invariant_violation
from facebook_monitor.webapp.read_model_invariants import inactive_invariant_target_ids
from facebook_monitor.webapp.read_model_invariants import read_mapper_value
from facebook_monitor.webapp.read_model_invariants import ReadModelInvariantMapperError


def target_exists(db_path: Path, target_id: str) -> bool:
    """檢查 target 是否存在，供 API route 回傳明確 404。"""

    try:
        with read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            if target_id in inactive_invariant_target_ids(
                app_context.repositories.targets.connection,
                violations=violations,
            ):
                return False
            if has_target_or_runtime_invariant_violation(target_id, violations):
                raise DashboardReadUnavailable("database invariant violation")
            return (
                read_mapper_value(
                    lambda: app_context.repositories.targets.get(target_id),
                    tables=("targets",),
                    violations=violations,
                )
                is not None
            )
    except sqlite3.OperationalError as exc:
        raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except ReadModelInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc


def list_hit_record_preview_rows(
    db_path: Path,
    target_id: str,
    *,
    limit: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_preview_limit,
    session_started_at: datetime | None = None,
) -> tuple[HitRecordPreviewRow, ...]:
    """讀取單一 target 的命中紀錄 preview rows。"""

    try:
        with read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            return tuple(
                HitRecordPreviewRow(entry=entry)
                for entry in read_mapper_value(
                    lambda: app_context.repositories.match_history.list_by_target(
                        target_id,
                        limit=limit,
                        recorded_since=session_started_at,
                    ),
                    tables=("match_history",),
                    violations=violations,
                )
            )
    except sqlite3.OperationalError as exc:
        raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except ReadModelInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc


def list_full_hit_record_rows(
    db_path: Path,
    target_id: str,
    *,
    limit: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.hit_record_full_limit,
    offset: int = 0,
) -> tuple[FullHitRecordRow, ...]:
    """讀取單一 target 的完整命中紀錄 rows。"""

    bounded_offset = max(int(offset), 0)
    try:
        with read_application_context(db_path) as app_context:
            violations = validate_database_invariants(
                app_context.repositories.targets.connection
            )
            entries = read_mapper_value(
                lambda: app_context.repositories.match_history.list_by_target(
                    target_id,
                    limit=limit,
                    offset=bounded_offset,
                ),
                tables=("match_history",),
                violations=violations,
            )
            notification_events = read_mapper_value(
                lambda: (
                    app_context.repositories.notification_events
                    .latest_sent_by_target_item_keys(
                        target_id,
                        [entry.item_key for entry in entries],
                    )
                ),
                tables=("notification_events",),
                violations=violations,
            )
    except sqlite3.OperationalError as exc:
        raise_dashboard_read_unavailable_if_locked(exc)
        raise
    except ReadModelInvariantMapperError as exc:
        raise DashboardReadUnavailable(str(exc)) from exc
    return tuple(
        FullHitRecordRow(
            entry=entry,
            sequence_number=bounded_offset + index + 1,
            notification_event=notification_events.get(entry.item_key),
        )
        for index, entry in enumerate(entries)
    )


def count_hit_records(
    db_path: Path,
    target_id: str,
    *,
    session_started_at: datetime | None = None,
) -> int:
    """計算單一 target 的命中紀錄筆數。"""

    try:
        with read_application_context(db_path) as app_context:
            _raise_if_hit_record_count_is_invariant_unsafe(
                app_context.repositories.match_history.connection,
                target_id=target_id,
                recorded_since=session_started_at,
            )
            return app_context.repositories.match_history.count_by_target(
                target_id,
                recorded_since=session_started_at,
            )
    except sqlite3.OperationalError as exc:
        raise_dashboard_read_unavailable_if_locked(exc)
        raise


def _raise_if_hit_record_count_is_invariant_unsafe(
    connection: sqlite3.Connection,
    *,
    target_id: str,
    recorded_since: datetime | None,
) -> None:
    """確認 count 查詢範圍內沒有會讓 hit-record mapper 失敗的 datetime。"""

    rows = connection.execute(
        """
        SELECT id, recorded_at, created_at
        FROM match_history
        WHERE target_id = ?
        """,
        (target_id,),
    ).fetchall()
    since_text = encode_datetime(recorded_since)
    for row in rows:
        if _hit_record_datetime_row_blocks_count(row, since_text=since_text):
            raise DashboardReadUnavailable("database invariant violation")


def _hit_record_datetime_row_blocks_count(
    row: sqlite3.Row,
    *,
    since_text: str,
) -> bool:
    """回傳單筆 match_history datetime 是否會讓對應 read model 不可讀。"""

    recorded_at = str(row["recorded_at"] or "")
    if since_text:
        if not recorded_at:
            return False
        if not _is_valid_datetime_text(recorded_at):
            return True
        if recorded_at < since_text:
            return False
    elif recorded_at and not _is_valid_datetime_text(recorded_at):
        return True
    created_at = str(row["created_at"] or "")
    return not created_at or not _is_valid_datetime_text(created_at)


def _is_valid_datetime_text(value: str) -> bool:
    """檢查 SQLite datetime text 是否可被既有 mapper decode。"""

    try:
        decode_datetime(value)
    except ValueError:
        return False
    return True
