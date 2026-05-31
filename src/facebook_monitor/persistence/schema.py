"""SQLite schema initialization and lightweight migrations。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.persistence.current_schema import create_current_schema
from facebook_monitor.persistence.current_schema import ensure_dashboard_revision_triggers
from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.persistence.sqlite_codec import write_schema_version

SCHEMA_VERSION = 33


def initialize_schema(connection: sqlite3.Connection) -> None:
    """建立目前 SQLite schema。"""

    existing_version = read_supported_schema_version(connection)
    create_current_schema(connection)

    migrate_or_mark_current_schema(connection, existing_version=existing_version)
    ensure_post_migration_schema_guards(connection)


def read_supported_schema_version(connection: sqlite3.Connection) -> int:
    """讀取並驗證 DB schema version 是否可由本版本處理。"""

    existing_version = read_schema_version(connection)
    has_existing_data_schema = has_existing_user_tables(connection)
    if existing_version == 0 and has_existing_data_schema:
        raise RuntimeError(
            "Unsupported SQLite schema version 0. Existing DBs must have a valid "
            "schema_metadata version for automatic migration."
        )
    if existing_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported SQLite schema version {existing_version}. "
            f"This app supports up to version {SCHEMA_VERSION}."
        )
    return existing_version


def migrate_or_mark_current_schema(
    connection: sqlite3.Connection,
    *,
    existing_version: int,
) -> None:
    """依既有 schema version 執行 migration 或標記新 DB 為目前版本。"""

    if 0 < existing_version < SCHEMA_VERSION:
        from facebook_monitor.persistence.migrations import run_known_migrations

        run_known_migrations(
            connection,
            from_version=existing_version,
            to_version=SCHEMA_VERSION,
        )
    elif existing_version < SCHEMA_VERSION:
        write_schema_version(connection, SCHEMA_VERSION)


def ensure_post_migration_schema_guards(connection: sqlite3.Connection) -> None:
    """建立需要在 migrations 後才可安全建立的 index / repair guard。"""

    ensure_dashboard_revision_triggers(connection)
    ensure_target_metadata_index(connection)
    ensure_target_scope_unique_index(connection)
    ensure_notification_outbox_dedupe_index(connection)
    drop_redundant_target_scope_index(connection)


def has_existing_user_tables(connection: sqlite3.Connection) -> bool:
    """判斷沒有 schema metadata 時，DB 是否已含既有產品資料表。"""

    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name <> 'schema_metadata'
        """
    ).fetchall()
    return bool(rows)


def ensure_target_scope_unique_index(connection: sqlite3.Connection) -> None:
    """確保 target kind/scope 在資料庫層唯一，避免 target-scoped state 分裂。"""

    repair_duplicate_target_scopes(connection)
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_targets_kind_scope_unique
        ON targets(target_kind, scope_id)
        """
    )


def ensure_target_metadata_index(connection: sqlite3.Connection) -> None:
    """建立 metadata refresh 狀態查詢 index；欄位需先由 migration 補齊。"""

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_targets_metadata_status_updated
        ON targets(metadata_status, updated_at)
        """
    )


def drop_redundant_target_scope_index(connection: sqlite3.Connection) -> None:
    """移除已被 unique index 涵蓋的舊普通 target scope index。"""

    connection.execute("DROP INDEX IF EXISTS idx_targets_kind_scope")


def ensure_notification_outbox_dedupe_index(connection: sqlite3.Connection) -> None:
    """在 v32 migration 補欄位後建立 outbox/dedupe 查詢索引。"""

    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(notification_outbox)").fetchall()
    }
    if "dedupe_id" not in columns:
        return
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_dedupe
            ON notification_outbox(dedupe_id)
        """
    )


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



