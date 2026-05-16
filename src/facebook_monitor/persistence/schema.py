"""SQLite schema initialization and lightweight migrations。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.persistence.sqlite_codec import write_schema_version

SCHEMA_VERSION = 25


def initialize_schema(connection: sqlite3.Connection) -> None:
    """建立目前 SQLite schema。"""

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
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS targets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            group_cover_image_url TEXT NOT NULL DEFAULT '',
            parent_post_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            metadata_status TEXT NOT NULL DEFAULT 'resolved',
            metadata_error TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL,
            paused INTEGER NOT NULL,
            worker_mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS target_configs (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
            exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
            min_refresh_sec INTEGER NOT NULL,
            max_refresh_sec INTEGER NOT NULL,
            jitter_enabled INTEGER NOT NULL,
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL,
            auto_load_more INTEGER NOT NULL,
            auto_adjust_sort INTEGER NOT NULL,
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS seen_items (
            scope_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, item_key)
        );

        CREATE TABLE IF NOT EXISTS scan_scope_state (
            scope_id TEXT PRIMARY KEY,
            initialized INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS match_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            permalink TEXT NOT NULL,
            include_rule TEXT NOT NULL,
            timestamp_text TEXT NOT NULL,
            notified_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS match_history_matches (
            history_id INTEGER NOT NULL REFERENCES match_history(id) ON DELETE CASCADE,
            match_order INTEGER NOT NULL,
            rule TEXT NOT NULL,
            PRIMARY KEY (history_id, match_order)
        );

        CREATE TABLE IF NOT EXISTS latest_scan_items (
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            scan_run_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_index INTEGER NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            permalink TEXT NOT NULL,
            matched_keyword TEXT NOT NULL,
            debug_metadata TEXT NOT NULL DEFAULT '{}',
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (target_id, item_key)
        );

        CREATE TABLE IF NOT EXISTS latest_scan_item_matches (
            target_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            match_order INTEGER NOT NULL,
            rule TEXT NOT NULL,
            PRIMARY KEY (target_id, item_key, match_order),
            FOREIGN KEY (target_id, item_key)
                REFERENCES latest_scan_items(target_id, item_key)
                ON UPDATE CASCADE
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL,
            error_message TEXT NOT NULL,
            worker_mode TEXT NOT NULL,
            metadata TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '',
            permalink TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            last_error TEXT NOT NULL,
            notification_event_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS target_runtime_state (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            desired_state TEXT NOT NULL,
            runtime_status TEXT NOT NULL,
            scan_requested_at TEXT NOT NULL DEFAULT '',
            last_enqueued_at TEXT NOT NULL DEFAULT '',
            last_started_at TEXT NOT NULL DEFAULT '',
            last_finished_at TEXT NOT NULL DEFAULT '',
            last_heartbeat_at TEXT NOT NULL,
            last_error TEXT NOT NULL,
            last_skip_reason TEXT NOT NULL DEFAULT '',
            enqueue_reason TEXT NOT NULL DEFAULT '',
            active_worker_id TEXT NOT NULL,
            active_page_id TEXT NOT NULL DEFAULT '',
            last_page_reloaded_at TEXT NOT NULL DEFAULT '',
            scan_guard_count INTEGER NOT NULL DEFAULT 0,
            display_next_due_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS global_notification_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sidebar_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            collapsed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sidebar_target_placements (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            sidebar_group_id TEXT REFERENCES sidebar_groups(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sidebar_group_config_templates (
            sidebar_group_id TEXT PRIMARY KEY REFERENCES sidebar_groups(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL DEFAULT '[]',
            exclude_keywords TEXT NOT NULL DEFAULT '[]',
            exclude_ignore_phrases TEXT NOT NULL DEFAULT '[]',
            min_refresh_sec INTEGER NOT NULL,
            max_refresh_sec INTEGER NOT NULL,
            jitter_enabled INTEGER NOT NULL,
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL,
            auto_load_more INTEGER NOT NULL,
            auto_adjust_sort INTEGER NOT NULL,
            enable_desktop_notification INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL,
            discord_webhook TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dashboard_revision (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            revision INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        INSERT OR IGNORE INTO dashboard_revision (id, revision, updated_at)
        VALUES (1, 0, '');

        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_created
            ON scan_runs(target_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_id_desc
            ON scan_runs(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_status_id_desc
            ON scan_runs(target_id, status, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_created
            ON notification_events(target_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_id_desc
            ON notification_events(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_channel_id_desc
            ON notification_events(target_id, channel, id DESC);
        CREATE INDEX IF NOT EXISTS idx_latest_scan_items_target_index
            ON latest_scan_items(target_id, item_index);
        CREATE INDEX IF NOT EXISTS idx_latest_scan_item_matches_target_item
            ON latest_scan_item_matches(target_id, item_key, match_order);
        CREATE INDEX IF NOT EXISTS idx_match_history_matches_history
            ON match_history_matches(history_id, match_order);
        CREATE INDEX IF NOT EXISTS idx_runtime_state_status_updated
            ON target_runtime_state(runtime_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_state_desired_updated
            ON target_runtime_state(desired_state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_status_updated
            ON notification_outbox(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_sidebar_groups_order
            ON sidebar_groups(sort_order);
        CREATE INDEX IF NOT EXISTS idx_sidebar_target_placements_group_order
            ON sidebar_target_placements(sidebar_group_id, sort_order);
        """
    )
    ensure_dashboard_revision_triggers(connection)
    if 0 < existing_version < SCHEMA_VERSION:
        from facebook_monitor.persistence.migrations import run_known_migrations

        run_known_migrations(
            connection,
            from_version=existing_version,
            to_version=SCHEMA_VERSION,
        )
    elif existing_version < SCHEMA_VERSION:
        write_schema_version(connection, SCHEMA_VERSION)
    ensure_target_metadata_index(connection)
    ensure_target_scope_unique_index(connection)
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


DASHBOARD_REVISION_TABLES = (
    "targets",
    "target_configs",
    "target_runtime_state",
    "scan_runs",
    "notification_events",
    "notification_outbox",
    "latest_scan_items",
    "match_history",
    "app_settings",
    "sidebar_groups",
    "sidebar_target_placements",
    "sidebar_group_config_templates",
)


def ensure_dashboard_revision_triggers(connection: sqlite3.Connection) -> None:
    """建立 dashboard revision bump triggers，讓 polling query 固定成本。"""

    stale_triggers = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name LIKE 'trg_dashboard_revision_%'
        """
    ).fetchall()
    for row in stale_triggers:
        trigger_name = str(row[0]).replace('"', '""')
        connection.execute(f'DROP TRIGGER IF EXISTS "{trigger_name}"')

    for table_name in DASHBOARD_REVISION_TABLES:
        for operation in ("INSERT", "UPDATE", "DELETE"):
            trigger_name = f"trg_dashboard_revision_{table_name}_{operation.lower()}"
            connection.execute(
                f"""
                CREATE TRIGGER {trigger_name}
                AFTER {operation} ON {table_name}
                BEGIN
                    UPDATE dashboard_revision
                    SET revision = revision + 1,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = 1;
                END
                """
            )


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
    connection.execute("DELETE FROM target_configs WHERE target_id = ?", (duplicate_id,))
    connection.execute("DELETE FROM target_runtime_state WHERE target_id = ?", (duplicate_id,))
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
        "notification_outbox",
    ):
        connection.execute(
            f"UPDATE {table_name} SET target_id = ? WHERE target_id = ?",
            (keep_id, duplicate_id),
        )
    connection.execute("DELETE FROM targets WHERE id = ?", (duplicate_id,))



