"""Shared SQLite persistence test helpers."""

from __future__ import annotations

import sqlite3

from facebook_monitor.persistence.repositories.global_notification_settings import (
    GlobalNotificationSettingsRepository,
)
from facebook_monitor.persistence.repositories.notification_outbox import (
    NotificationOutboxRepository,
)
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec


PLAINTEXT_SECRET_CODEC = PlaintextSecretCodec()


def target_config_repository(connection: sqlite3.Connection) -> TargetConfigRepository:
    """測試用明文 secret codec；正式路徑由 application context 注入加密 codec。"""

    return TargetConfigRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC)


def global_notification_settings_repository(
    connection: sqlite3.Connection,
) -> GlobalNotificationSettingsRepository:
    """測試用明文 global notification repository。"""

    return GlobalNotificationSettingsRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC)


def notification_outbox_repository(connection: sqlite3.Connection) -> NotificationOutboxRepository:
    """測試用明文 notification outbox repository。"""

    return NotificationOutboxRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC)


def table_count(connection: sqlite3.Connection, table_name: str) -> int:
    """回傳指定測試資料表目前筆數。"""

    row = connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0])


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """回傳指定 table 是否存在。"""

    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def table_sql(connection: sqlite3.Connection, table_name: str) -> str:
    """回傳 sqlite_master 中的 table 建表 SQL。"""

    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return str(row["sql"] if row is not None else "")


def create_raw_v12_missing_columns_schema(connection: sqlite3.Connection) -> None:
    """建立 schema_metadata=12 但缺少歷史欄位的代表性 DB。"""

    connection.executescript(
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_metadata (key, value) VALUES ('version', '12');

        CREATE TABLE target_configs (
            target_id TEXT PRIMARY KEY,
            include_keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
            min_refresh_sec INTEGER NOT NULL,
            max_refresh_sec INTEGER NOT NULL,
            jitter_enabled INTEGER NOT NULL,
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL,
            auto_load_more INTEGER NOT NULL,
            auto_adjust_sort INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL
        );

        CREATE TABLE group_configs (
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
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL
        );

        CREATE TABLE latest_scan_items (
            target_id TEXT NOT NULL,
            scan_run_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_index INTEGER NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            permalink TEXT NOT NULL,
            matched_keyword TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (target_id, item_key)
        );

        CREATE TABLE notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL UNIQUE,
            target_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            permalink TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            last_error TEXT NOT NULL,
            notification_event_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE target_runtime_state (
            target_id TEXT PRIMARY KEY,
            desired_state TEXT NOT NULL,
            runtime_status TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            last_error TEXT NOT NULL,
            active_worker_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def table_has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """回傳 table 是否含指定欄位。"""

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def create_raw_v10_fixture_schema(connection: sqlite3.Connection) -> None:
    """建立不經 current schema helper 的 v10 代表性舊 DB。"""

    connection.executescript(
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_metadata (key, value) VALUES ('version', '10');

        CREATE TABLE targets (
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

        CREATE TABLE target_configs (
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
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL
        );

        CREATE TABLE seen_items (
            scope_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, item_key)
        );

        CREATE TABLE match_history (
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

        CREATE TABLE latest_scan_items (
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            scan_run_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_index INTEGER NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            permalink TEXT NOT NULL,
            matched_keyword TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (target_id, item_key)
        );

        CREATE TABLE scan_runs (
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

        CREATE TABLE notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE target_runtime_state (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            desired_state TEXT NOT NULL,
            runtime_status TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            last_error TEXT NOT NULL,
            active_worker_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    now = "2026-01-01T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, parent_post_id,
            scope_id, canonical_url, enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            "legacy group",
            "posts",
            "111",
            "legacy group",
            "",
            "111",
            "https://www.facebook.com/groups/111",
            1,
            0,
            "headless",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, parent_post_id,
            scope_id, canonical_url, enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "comments-target",
            "legacy comments",
            "comments",
            "111",
            "legacy group",
            "999",
            "111:post:999:comments",
            "https://www.facebook.com/groups/111/posts/999",
            1,
            1,
            "headless",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO target_configs (
            target_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_ntfy, ntfy_topic
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            '["legacy"]',
            "[]",
            30,
            120,
            0,
            90,
            7,
            1,
            1,
            1,
            "legacy-topic",
        ),
    )
    connection.execute(
        """
        INSERT INTO target_runtime_state (
            target_id, desired_state, runtime_status, last_heartbeat_at,
            last_error, active_worker_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("posts-target", "stopped", "paused", now, "", "", now),
    )
    connection.execute(
        """
        INSERT INTO scan_runs (
            id, target_id, started_at, finished_at, status, item_count,
            matched_count, error_message, worker_mode, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "posts-target", now, now, "success", 1, 1, "", "headless", "{}"),
    )
    connection.execute(
        """
        INSERT INTO latest_scan_items (
            target_id, scan_run_id, item_kind, item_key, item_index,
            author, text, permalink, matched_keyword, scanned_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            1,
            "post",
            "legacy-item",
            0,
            "author",
            "legacy text",
            "https://www.facebook.com/groups/111/posts/1",
            "legacy",
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO seen_items (
            scope_id, item_key, item_kind, parent_post_id, comment_id,
            first_seen_at, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("111", "legacy-item", "post", "", "", now, now),
    )
    connection.execute(
        """
        INSERT INTO notification_events (
            target_id, item_key, channel, status, message, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("posts-target", "legacy-item", "ntfy", "sent", "legacy sent", now),
    )
    connection.execute(
        """
        INSERT INTO match_history (
            target_id, group_id, group_name, item_kind, parent_post_id,
            comment_id, item_key, author, text, permalink, include_rule,
            timestamp_text, notified_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            "111",
            "legacy group",
            "post",
            "",
            "",
            "legacy-item",
            "author",
            "legacy text",
            "https://www.facebook.com/groups/111/posts/1",
            "legacy",
            "",
            now,
            now,
        ),
    )
