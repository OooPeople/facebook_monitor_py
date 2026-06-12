"""Support bundle notification collectors。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import sqlite3

from facebook_monitor.diagnostics._support_bundle_constants import RECENT_NOTIFICATION_SAMPLE_LIMIT
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_names
from facebook_monitor.notifications.failure_taxonomy import classify_notification_failure


def _notification_diagnostics_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立 notification outbox/events 的安全摘要。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing"}
    with _readonly_connection(db_path) as connection:
        table_names = _table_names(connection)
        payload: dict[str, object] = {"available": True}
        if "notification_outbox" in table_names:
            payload["outbox_counts"] = _notification_outbox_counts(connection, aliases)
            payload["failure_category_counts"] = _notification_failure_category_counts(
                connection,
            )
            payload["failed_outbox_samples"] = _failed_outbox_samples(connection, aliases)
        else:
            payload["outbox_counts"] = []
            payload["failure_category_counts"] = {}
            payload["failed_outbox_samples"] = []
        if "notification_events" in table_names:
            payload["event_counts"] = _notification_event_counts(connection, aliases)
            payload["recent_events"] = _recent_notification_events(connection, aliases)
        else:
            payload["event_counts"] = []
            payload["recent_events"] = []
    return payload

def _notification_outbox_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """依 target/channel/event kind/status 彙總 outbox。"""

    rows = connection.execute(
        """
        SELECT
            target_id, channel, event_kind, status,
            COUNT(*) AS count,
            MIN(updated_at) AS oldest_updated_at,
            MAX(attempts) AS max_attempts
        FROM notification_outbox
        GROUP BY target_id, channel, event_kind, status
        ORDER BY target_id, channel, event_kind, status
        """
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "count": int(row["count"] or 0),
            "oldest_updated_at": str(row["oldest_updated_at"] or ""),
            "max_attempts": int(row["max_attempts"] or 0),
        }
        for row in rows
    ]


def _failed_outbox_samples(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳最近 failed outbox sample，不輸出 endpoint/title/message/permalink。"""

    rows = connection.execute(
        """
        SELECT id, target_id, item_key, channel, event_kind, status, attempts,
               failure_reason, failure_count, last_error, source_scan_run_id,
               created_at, updated_at
        FROM notification_outbox
        WHERE status IN ('failed', 'processing_failed')
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (RECENT_NOTIFICATION_SAMPLE_LIMIT,),
    ).fetchall()
    return [
        {
            "outbox": aliases.alias("outbox", row["id"]),
            "target": aliases.alias("target", row["target_id"]),
            "item": aliases.alias("item", row["item_key"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "attempts": int(row["attempts"] or 0),
            "failure_reason": _freeform_summary(
                str(row["failure_reason"] or ""),
                aliases=aliases,
            ),
            "failure_category": classify_notification_failure(
                channel=str(row["channel"] or ""),
                failure_reason=str(row["failure_reason"] or ""),
                last_error=str(row["last_error"] or ""),
            ),
            "failure_count": int(row["failure_count"] or 0),
            "last_error": _freeform_summary(
                str(row["last_error"] or ""),
                aliases=aliases,
            ),
            "source_scan_run": aliases.alias("scan_run", row["source_scan_run_id"]),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def _notification_failure_category_counts(
    connection: sqlite3.Connection,
) -> dict[str, int]:
    """彙總 failed outbox 的衍生 failure category。"""

    rows = connection.execute(
        """
        SELECT channel, failure_reason, last_error
        FROM notification_outbox
        WHERE status IN ('failed', 'processing_failed')
        """
    ).fetchall()
    counts = Counter(
        classify_notification_failure(
            channel=str(row["channel"] or ""),
            failure_reason=str(row["failure_reason"] or ""),
            last_error=str(row["last_error"] or ""),
        )
        for row in rows
    )
    return dict(sorted(counts.items()))


def _notification_event_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """依 target/channel/event kind/status 彙總 notification events。"""

    rows = connection.execute(
        """
        SELECT target_id, channel, event_kind, status,
               COUNT(*) AS count, MAX(created_at) AS latest_created_at
        FROM notification_events
        GROUP BY target_id, channel, event_kind, status
        ORDER BY target_id, channel, event_kind, status
        """
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "count": int(row["count"] or 0),
            "latest_created_at": str(row["latest_created_at"] or ""),
        }
        for row in rows
    ]


def _recent_notification_events(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳最近通知事件摘要，不輸出 message 原文。"""

    rows = connection.execute(
        """
        SELECT id, target_id, item_key, channel, event_kind, status,
               source_scan_run_id, failure_reason, failure_count, created_at
        FROM notification_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (RECENT_NOTIFICATION_SAMPLE_LIMIT,),
    ).fetchall()
    return [
        {
            "event": aliases.alias("notification_event", row["id"]),
            "target": aliases.alias("target", row["target_id"]),
            "item": aliases.alias("item", row["item_key"]),
            "channel": str(row["channel"] or ""),
            "event_kind": str(row["event_kind"] or ""),
            "status": str(row["status"] or ""),
            "source_scan_run": aliases.alias("scan_run", row["source_scan_run_id"]),
            "failure_reason": _freeform_summary(
                str(row["failure_reason"] or ""),
                aliases=aliases,
            ),
            "failure_count": int(row["failure_count"] or 0),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]


__all__ = [
    "_notification_diagnostics_payload",
]
