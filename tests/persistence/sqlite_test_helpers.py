"""Shared SQLite persistence test helpers."""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.persistence.repositories.notification_outbox import (
    NotificationOutboxRepository,
)
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec


PLAINTEXT_SECRET_CODEC = PlaintextSecretCodec()


def target_config_repository(connection: sqlite3.Connection) -> TargetConfigRepository:
    """測試用明文 secret codec；正式路徑由 application context 注入加密 codec。"""

    return TargetConfigRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC)


def save_target_config_for_test(
    connection: sqlite3.Connection,
    target_id: str,
    config: TargetConfig,
) -> TargetConfig:
    """用正式 repository API 建立 target config fixture。"""

    return target_config_repository(connection).save_for_target_id(target_id, config)


def get_target_config_for_test(
    connection: sqlite3.Connection,
    target_id: str,
) -> TargetConfig | None:
    """用正式 repository API 讀取 target config fixture。"""

    return target_config_repository(connection).get_for_target_id(target_id)


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


def table_has_column(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """回傳 table 是否含指定欄位。"""

    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def create_min_supported_v35_fixture_schema(connection: sqlite3.Connection) -> None:
    """建立最低支援版本 v35 的代表性 DB。"""

    connection.executescript(
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_metadata (key, value) VALUES ('version', '35');

        CREATE TABLE targets (
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
            display_text TEXT NOT NULL DEFAULT '',
            permalink TEXT NOT NULL,
            include_rule TEXT NOT NULL,
            timestamp_text TEXT NOT NULL,
            notified_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    now = "2026-01-01T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, group_cover_image_url,
            parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
            enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            "legacy group",
            "posts",
            "111",
            "legacy group",
            "",
            "",
            "111",
            "https://www.facebook.com/groups/111",
            "resolved",
            "",
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
            id, name, target_kind, group_id, group_name, group_cover_image_url,
            parent_post_id, scope_id, canonical_url, metadata_status, metadata_error,
            enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "comments-target",
            "legacy comments",
            "comments",
            "111",
            "legacy group",
            "",
            "999",
            "111:post:999:comments",
            "https://www.facebook.com/groups/111/posts/999",
            "resolved",
            "",
            1,
            1,
            "headless",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO match_history (
            target_id, group_id, group_name, item_kind, parent_post_id,
            comment_id, item_key, author, text, display_text, permalink,
            include_rule, timestamp_text, notified_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            "legacy text",
            "https://www.facebook.com/groups/111/posts/1",
            "legacy",
            "",
            now,
            now,
        ),
    )
