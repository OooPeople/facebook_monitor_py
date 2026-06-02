"""SQLite schema repair helpers。

職責：修復歷史資料中 target kind/scope 重複造成的 target-scoped state
分裂。這些操作和 schema bootstrap / index guard 維護節奏不同，因此獨立
於 `schema.py`。
"""

from __future__ import annotations

import sqlite3


def repair_duplicate_target_scopes(connection: sqlite3.Connection) -> None:
    """合併歷史上可能產生的重複 target scope，保留最早建立的 target。"""

    duplicate_groups = connection.execute(
        """
        SELECT target_kind, scope_id, MIN(created_at) AS first_created_at, COUNT(*) AS count
        FROM targets
        GROUP BY target_kind, scope_id
        HAVING count > 1
        """
    ).fetchall()
    for group in duplicate_groups:
        rows = connection.execute(
            """
            SELECT id
            FROM targets
            WHERE target_kind = ? AND scope_id = ?
            ORDER BY created_at, id
            """,
            (group["target_kind"], group["scope_id"]),
        ).fetchall()
        if len(rows) <= 1:
            continue
        keep_id = str(rows[0]["id"])
        duplicate_ids = [str(row["id"]) for row in rows[1:]]
        for duplicate_id in duplicate_ids:
            _merge_duplicate_target(connection, keep_id=keep_id, duplicate_id=duplicate_id)


def _merge_duplicate_target(connection: sqlite3.Connection, *, keep_id: str, duplicate_id: str) -> None:
    """把 duplicate target 的 runtime/history 資料併回保留 target 後刪除 duplicate。"""

    if keep_id == duplicate_id:
        return
    for table_name in (
        "target_configs",
        "target_runtime_state",
        "target_cover_image_refresh_state",
        "sidebar_target_placements",
        "target_dedupe_state",
    ):
        _move_single_target_row_if_keep_missing(
            connection,
            table_name=table_name,
            keep_id=keep_id,
            duplicate_id=duplicate_id,
        )
    _merge_duplicate_logical_items(
        connection,
        keep_id=keep_id,
        duplicate_id=duplicate_id,
    )
    _merge_duplicate_notification_dedupe(
        connection,
        keep_id=keep_id,
        duplicate_id=duplicate_id,
    )
    connection.execute(
        """
        DELETE FROM latest_scan_items
        WHERE target_id = ?
          AND item_key IN (
              SELECT item_key FROM latest_scan_items WHERE target_id = ?
          )
        """,
        (duplicate_id, keep_id),
    )
    for table_name in (
        "latest_scan_items",
        "scan_runs",
        "match_history",
        "notification_events",
    ):
        connection.execute(
            f"UPDATE {table_name} SET target_id = ? WHERE target_id = ?",
            (keep_id, duplicate_id),
        )
    _merge_duplicate_notification_outbox(
        connection,
        keep_id=keep_id,
        duplicate_id=duplicate_id,
    )
    connection.execute("DELETE FROM targets WHERE id = ?", (duplicate_id,))


def _merge_duplicate_notification_outbox(
    connection: sqlite3.Connection,
    *,
    keep_id: str,
    duplicate_id: str,
) -> None:
    """合併 duplicate target outbox，並同步重寫 target-scoped idempotency key。"""

    rows = connection.execute(
        """
        SELECT id, item_key, channel
        FROM notification_outbox
        WHERE target_id = ?
        ORDER BY id
        """,
        (duplicate_id,),
    ).fetchall()
    for row in rows:
        item_key = str(row["item_key"])
        channel = str(row["channel"])
        duplicate_row_id = int(row["id"])
        existing = connection.execute(
            """
            SELECT 1
            FROM notification_outbox
            WHERE target_id = ?
              AND item_key = ?
              AND channel = ?
            """,
            (keep_id, item_key, channel),
        ).fetchone()
        if existing is not None:
            connection.execute(
                "DELETE FROM notification_outbox WHERE id = ?",
                (duplicate_row_id,),
            )
            continue
        try:
            connection.execute(
                """
                UPDATE notification_outbox
                SET target_id = ?,
                    idempotency_key = ?
                WHERE id = ?
                """,
                (keep_id, f"{keep_id}:{item_key}:{channel}", duplicate_row_id),
            )
        except sqlite3.IntegrityError:
            connection.execute(
                "DELETE FROM notification_outbox WHERE id = ?",
                (duplicate_row_id,),
            )


def _move_single_target_row_if_keep_missing(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    keep_id: str,
    duplicate_id: str,
) -> None:
    """搬移單列 target-scoped state；保留端已有資料時才刪 duplicate row。"""

    keep_row = connection.execute(
        f"SELECT 1 FROM {table_name} WHERE target_id = ?",
        (keep_id,),
    ).fetchone()
    if keep_row is None:
        connection.execute(
            f"UPDATE {table_name} SET target_id = ? WHERE target_id = ?",
            (keep_id, duplicate_id),
        )
        return
    connection.execute(f"DELETE FROM {table_name} WHERE target_id = ?", (duplicate_id,))


def _merge_duplicate_logical_items(
    connection: sqlite3.Connection,
    *,
    keep_id: str,
    duplicate_id: str,
) -> None:
    """合併 duplicate target 的 logical item state，避免 scope repair 遺失去重資料。"""

    if not _table_exists(connection, "logical_items"):
        return
    rows = connection.execute(
        """
        SELECT id, dedupe_epoch
        FROM logical_items
        WHERE target_id = ?
        ORDER BY id
        """,
        (duplicate_id,),
    ).fetchall()
    for row in rows:
        logical_item_id = int(row["id"])
        alias_rows = connection.execute(
            """
            SELECT alias_key, dedupe_epoch
            FROM logical_item_aliases
            WHERE logical_item_id = ?
            """,
            (logical_item_id,),
        ).fetchall()
        has_alias_conflict = False
        for alias_row in alias_rows:
            conflict = connection.execute(
                """
                SELECT logical_item_id
                FROM logical_item_aliases
                WHERE target_id = ?
                  AND dedupe_epoch = ?
                  AND alias_key = ?
                LIMIT 1
                """,
                (keep_id, alias_row["dedupe_epoch"], alias_row["alias_key"]),
            ).fetchone()
            if conflict is not None:
                has_alias_conflict = True
                _move_logical_notification_dedupe(
                    connection,
                    from_logical_item_id=logical_item_id,
                    to_logical_item_id=int(conflict["logical_item_id"]),
                )
                break
        if has_alias_conflict:
            connection.execute("DELETE FROM logical_items WHERE id = ?", (logical_item_id,))
            continue
        connection.execute(
            "UPDATE logical_items SET target_id = ? WHERE id = ?",
            (keep_id, logical_item_id),
        )
        connection.execute(
            "UPDATE logical_item_aliases SET target_id = ? WHERE logical_item_id = ?",
            (keep_id, logical_item_id),
        )


def _move_logical_notification_dedupe(
    connection: sqlite3.Connection,
    *,
    from_logical_item_id: int,
    to_logical_item_id: int,
) -> None:
    """把被合併 logical item 的 notification dedupe 指向保留 logical item。"""

    if from_logical_item_id == to_logical_item_id:
        return
    if not _table_exists(connection, "notification_dedupe"):
        return
    rows = connection.execute(
        """
        SELECT id, target_id, dedupe_epoch, event_kind, channel
        FROM notification_dedupe
        WHERE logical_item_id = ?
          AND event_kind = 'match'
        """,
        (from_logical_item_id,),
    ).fetchall()
    new_subject_key = f"logical:{to_logical_item_id}"
    for row in rows:
        dedupe_id = int(row["id"])
        existing = connection.execute(
            """
            SELECT id
            FROM notification_dedupe
            WHERE target_id = ?
              AND dedupe_epoch = ?
              AND event_kind = ?
              AND channel = ?
              AND subject_key = ?
              AND id <> ?
            """,
            (
                row["target_id"],
                row["dedupe_epoch"],
                row["event_kind"],
                row["channel"],
                new_subject_key,
                dedupe_id,
            ),
        ).fetchone()
        if existing is not None:
            connection.execute(
                "UPDATE notification_outbox SET dedupe_id = ? WHERE dedupe_id = ?",
                (int(existing["id"]), dedupe_id),
            )
            connection.execute("DELETE FROM notification_dedupe WHERE id = ?", (dedupe_id,))
            continue
        connection.execute(
            """
            UPDATE notification_dedupe
            SET logical_item_id = ?,
                subject_key = ?
            WHERE id = ?
            """,
            (to_logical_item_id, new_subject_key, dedupe_id),
        )


def _merge_duplicate_notification_dedupe(
    connection: sqlite3.Connection,
    *,
    keep_id: str,
    duplicate_id: str,
) -> None:
    """合併 duplicate target 的 notification dedupe rows。"""

    if not _table_exists(connection, "notification_dedupe"):
        return
    rows = connection.execute(
        """
        SELECT id, dedupe_epoch, event_kind, channel, subject_key
        FROM notification_dedupe
        WHERE target_id = ?
        ORDER BY id
        """,
        (duplicate_id,),
    ).fetchall()
    for row in rows:
        dedupe_id = int(row["id"])
        existing = connection.execute(
            """
            SELECT id
            FROM notification_dedupe
            WHERE target_id = ?
              AND dedupe_epoch = ?
              AND event_kind = ?
              AND channel = ?
              AND subject_key = ?
            """,
            (
                keep_id,
                row["dedupe_epoch"],
                row["event_kind"],
                row["channel"],
                row["subject_key"],
            ),
        ).fetchone()
        if existing is not None:
            connection.execute(
                "UPDATE notification_outbox SET dedupe_id = ? WHERE dedupe_id = ?",
                (int(existing["id"]), dedupe_id),
            )
            connection.execute("DELETE FROM notification_dedupe WHERE id = ?", (dedupe_id,))
            continue
        connection.execute(
            "UPDATE notification_dedupe SET target_id = ? WHERE id = ?",
            (keep_id, dedupe_id),
        )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """回傳 schema repair 需要的 table 是否存在。"""

    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None
