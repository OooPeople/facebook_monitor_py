"""SQLite schema initialization and lightweight migrations。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.persistence.sqlite_codec import write_schema_version

SCHEMA_VERSION = 12


def initialize_schema(connection: sqlite3.Connection) -> None:
    """建立目前 SQLite schema。"""

    existing_version = read_schema_version(connection)
    has_existing_data_schema = has_existing_user_tables(connection)
    if (
        existing_version == 0
        and has_existing_data_schema
        and not has_schema_metadata_table(connection)
    ):
        raise RuntimeError(
            "Unsupported SQLite schema version 0. Existing DBs must have schema_metadata "
            "for automatic migration."
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
            parent_post_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
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

        CREATE TABLE IF NOT EXISTS group_configs (
            group_id TEXT PRIMARY KEY,
            include_keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
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

        CREATE TABLE IF NOT EXISTS dashboard_revision (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            revision INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        INSERT OR IGNORE INTO dashboard_revision (id, revision, updated_at)
        VALUES (1, 0, '');

        CREATE INDEX IF NOT EXISTS idx_targets_kind_scope
            ON targets(target_kind, scope_id);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_created
            ON scan_runs(target_id, started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_created
            ON notification_events(target_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_latest_scan_items_target_index
            ON latest_scan_items(target_id, item_index);
        CREATE INDEX IF NOT EXISTS idx_runtime_state_status_updated
            ON target_runtime_state(runtime_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_runtime_state_desired_updated
            ON target_runtime_state(desired_state, updated_at);
        CREATE INDEX IF NOT EXISTS idx_notification_outbox_status_updated
            ON notification_outbox(status, updated_at);
        """
    )
    ensure_dashboard_revision_triggers(connection)
    ensure_column(
        connection,
        table_name="notification_outbox",
        column_name="endpoint",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="latest_scan_items",
        column_name="debug_metadata",
        definition="TEXT NOT NULL DEFAULT '{}'",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="scan_requested_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_skip_reason",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_enqueued_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_started_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_finished_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="enqueue_reason",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="active_page_id",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="last_page_reloaded_at",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="target_runtime_state",
        column_name="scan_guard_count",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="target_configs",
        column_name="enable_desktop_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="target_configs",
        column_name="enable_discord_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="target_configs",
        column_name="discord_webhook",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    ensure_column(
        connection,
        table_name="group_configs",
        column_name="enable_desktop_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="group_configs",
        column_name="enable_discord_notification",
        definition="INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        connection,
        table_name="group_configs",
        column_name="discord_webhook",
        definition="TEXT NOT NULL DEFAULT ''",
    )
    if 0 < existing_version < SCHEMA_VERSION:
        from facebook_monitor.persistence.migrations import run_known_migrations

        run_known_migrations(
            connection,
            from_version=existing_version,
            to_version=SCHEMA_VERSION,
        )
    elif existing_version < SCHEMA_VERSION:
        write_schema_version(connection, SCHEMA_VERSION)


def ensure_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """確保既有 SQLite table 有指定欄位，供小型 schema migration 使用。"""

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(row["name"] == column_name for row in rows):
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


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


def has_schema_metadata_table(connection: sqlite3.Connection) -> bool:
    """判斷 DB 是否已有 schema metadata table。"""

    row = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_metadata'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def ensure_dashboard_revision_triggers(connection: sqlite3.Connection) -> None:
    """建立 dashboard revision bump triggers，讓 polling query 固定成本。"""

    for table_name in (
        "targets",
        "group_configs",
        "target_runtime_state",
        "scan_runs",
        "notification_events",
        "latest_scan_items",
        "match_history",
    ):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            trigger_name = f"trg_dashboard_revision_{table_name}_{operation.lower()}"
            connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
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



