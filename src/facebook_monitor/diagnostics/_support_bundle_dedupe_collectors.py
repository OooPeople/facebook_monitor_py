"""Support bundle dedupe collectors。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_names


def _dedupe_summary_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立 baseline / dedupe 相關狀態摘要。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing"}
    with _readonly_connection(db_path) as connection:
        table_names = _table_names(connection)
        payload: dict[str, object] = {"available": True}
        if "scan_scope_state" in table_names:
            rows = connection.execute(
                """
                SELECT scope_id, initialized, updated_at
                FROM scan_scope_state
                ORDER BY updated_at DESC
                LIMIT 100
                """
            ).fetchall()
            payload["scan_scope_state"] = [
                {
                    "scope": aliases.alias("scope", row["scope_id"]),
                    "initialized": bool(row["initialized"]),
                    "updated_at": str(row["updated_at"] or ""),
                }
                for row in rows
            ]
        else:
            payload["scan_scope_state"] = []
        payload["target_dedupe_state"] = _target_dedupe_state_summary(connection, aliases)
        payload["logical_item_counts"] = _logical_item_counts(connection, aliases)
        payload["seen_item_counts"] = _seen_item_counts(connection, aliases)
        payload["notification_dedupe_counts"] = _notification_dedupe_counts(
            connection,
            aliases,
        )
    return payload

def _target_dedupe_state_summary(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 target dedupe epoch 摘要。"""

    if "target_dedupe_state" not in _table_names(connection):
        return []
    rows = connection.execute(
        "SELECT target_id, dedupe_epoch, updated_at FROM target_dedupe_state ORDER BY target_id"
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "dedupe_epoch": int(row["dedupe_epoch"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        }
        for row in rows
    ]


def _logical_item_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 logical item / alias count 摘要。"""

    table_names = _table_names(connection)
    if "logical_items" not in table_names:
        return []
    rows = connection.execute(
        """
        SELECT target_id, item_kind, COUNT(*) AS count,
               MIN(first_seen_at) AS oldest_first_seen_at,
               MAX(last_seen_at) AS newest_last_seen_at
        FROM logical_items
        GROUP BY target_id, item_kind
        ORDER BY target_id, item_kind
        """
    ).fetchall()
    alias_counts: dict[str, int] = {}
    if "logical_item_aliases" in table_names:
        for row in connection.execute(
            """
            SELECT target_id, COUNT(*) AS count
            FROM logical_item_aliases
            GROUP BY target_id
            """
        ).fetchall():
            alias_counts[str(row["target_id"])] = int(row["count"] or 0)
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "item_kind": str(row["item_kind"] or ""),
            "count": int(row["count"] or 0),
            "alias_count_for_target": alias_counts.get(str(row["target_id"]), 0),
            "oldest_first_seen_at": str(row["oldest_first_seen_at"] or ""),
            "newest_last_seen_at": str(row["newest_last_seen_at"] or ""),
        }
        for row in rows
    ]


def _notification_dedupe_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 notification dedupe ledger count 摘要。"""

    if "notification_dedupe" not in _table_names(connection):
        return []
    rows = connection.execute(
        """
        SELECT target_id, event_kind, channel, item_kind, status,
               COUNT(*) AS count, MAX(last_deduped_at) AS latest_deduped_at
        FROM notification_dedupe
        GROUP BY target_id, event_kind, channel, item_kind, status
        ORDER BY target_id, event_kind, channel, item_kind, status
        """
    ).fetchall()
    return [
        {
            "target": aliases.alias("target", row["target_id"]),
            "event_kind": str(row["event_kind"] or ""),
            "channel": str(row["channel"] or ""),
            "item_kind": str(row["item_kind"] or ""),
            "status": str(row["status"] or ""),
            "count": int(row["count"] or 0),
            "latest_deduped_at": str(row["latest_deduped_at"] or ""),
        }
        for row in rows
    ]


def _seen_item_counts(
    connection: sqlite3.Connection,
    aliases: _SupportBundleAliases,
) -> list[dict[str, object]]:
    """回傳 legacy seen item count 摘要。"""

    if "seen_items" not in _table_names(connection):
        return []
    rows = connection.execute(
        """
        SELECT scope_id, item_kind, COUNT(*) AS count,
               MIN(first_seen_at) AS oldest_first_seen_at,
               MAX(last_seen_at) AS newest_last_seen_at
        FROM seen_items
        GROUP BY scope_id, item_kind
        ORDER BY scope_id, item_kind
        """
    ).fetchall()
    return [
        {
            "scope": aliases.alias("scope", row["scope_id"]),
            "item_kind": str(row["item_kind"] or ""),
            "count": int(row["count"] or 0),
            "oldest_first_seen_at": str(row["oldest_first_seen_at"] or ""),
            "newest_last_seen_at": str(row["newest_last_seen_at"] or ""),
        }
        for row in rows
    ]


__all__ = [
    "_dedupe_summary_payload",
]
