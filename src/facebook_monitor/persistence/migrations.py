"""SQLite migration chain。

職責：保存明確版本鏈的 migration entrypoint。既有 DB 欄位補齊必須進
本模組的版本鏈，不得另建 current-schema repair 平行路徑。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.persistence.sqlite_codec import encode_keywords


Migration = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class MigrationColumn:
    """描述 migration 需要補上的欄位。"""

    table_name: str
    column_name: str
    definition: str


V10_TARGET_CONFIG_NOTIFICATION_COLUMNS = (
    MigrationColumn(
        "target_configs",
        "enable_desktop_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "target_configs",
        "enable_discord_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "target_configs",
        "discord_webhook",
        "TEXT NOT NULL DEFAULT ''",
    ),
)


V12_TO_13_COLUMNS = (
    MigrationColumn(
        "notification_outbox",
        "endpoint",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "latest_scan_items",
        "debug_metadata",
        "TEXT NOT NULL DEFAULT '{}'",
    ),
    MigrationColumn(
        "target_runtime_state",
        "scan_requested_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_skip_reason",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_enqueued_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_started_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_finished_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "enqueue_reason",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "active_page_id",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "last_page_reloaded_at",
        "TEXT NOT NULL DEFAULT ''",
    ),
    MigrationColumn(
        "target_runtime_state",
        "scan_guard_count",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    *V10_TARGET_CONFIG_NOTIFICATION_COLUMNS,
    MigrationColumn(
        "group_configs",
        "enable_desktop_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "group_configs",
        "enable_discord_notification",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    MigrationColumn(
        "group_configs",
        "discord_webhook",
        "TEXT NOT NULL DEFAULT ''",
    ),
)


def migrate_10_to_11(connection: sqlite3.Connection) -> None:
    """將舊 target-scoped config 搬到 group-scoped config。"""

    ensure_legacy_group_configs_table(connection)
    for column in V10_TARGET_CONFIG_NOTIFICATION_COLUMNS:
        add_column_if_missing(connection, column)
    connection.execute(
        """
        INSERT OR IGNORE INTO group_configs (
            group_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_desktop_notification,
            enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
        )
        SELECT
            targets.group_id,
            target_configs.include_keywords,
            target_configs.exclude_keywords,
            target_configs.min_refresh_sec,
            target_configs.max_refresh_sec,
            target_configs.jitter_enabled,
            target_configs.fixed_refresh_sec,
            target_configs.max_items_per_scan,
            target_configs.auto_load_more,
            target_configs.auto_adjust_sort,
            target_configs.enable_desktop_notification,
            target_configs.enable_ntfy,
            target_configs.ntfy_topic,
            target_configs.enable_discord_notification,
            target_configs.discord_webhook
        FROM target_configs
        JOIN targets ON targets.id = target_configs.target_id
        WHERE targets.group_id <> ''
        """
    )


def migrate_11_to_12(connection: sqlite3.Connection) -> None:
    """將舊 runtime_status=paused 正規化為 executor 狀態 idle。"""

    connection.execute(
        """
        UPDATE target_runtime_state
        SET runtime_status = 'idle'
        WHERE runtime_status = 'paused'
        """
    )


def migrate_12_to_13(connection: sqlite3.Connection) -> None:
    """將歷史 current-schema 補欄位收斂進正式 migration 鏈。"""

    for column in V12_TO_13_COLUMNS:
        add_column_if_missing(connection, column)


def migrate_13_to_14(connection: sqlite3.Connection) -> None:
    """將正式 config owner 從 group_configs 改回 target_configs[target_id]。"""

    ensure_legacy_group_configs_table(connection)
    for column in V10_TARGET_CONFIG_NOTIFICATION_COLUMNS:
        add_column_if_missing(connection, column)
    for column in (
        MigrationColumn(
            "group_configs",
            "enable_desktop_notification",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        MigrationColumn(
            "group_configs",
            "enable_discord_notification",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        MigrationColumn(
            "group_configs",
            "discord_webhook",
            "TEXT NOT NULL DEFAULT ''",
        ),
    ):
        add_column_if_missing(connection, column)
    connection.execute(
        """
        INSERT INTO target_configs (
            target_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_desktop_notification,
            enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
        )
        SELECT
            targets.id,
            group_configs.include_keywords,
            group_configs.exclude_keywords,
            group_configs.min_refresh_sec,
            group_configs.max_refresh_sec,
            group_configs.jitter_enabled,
            group_configs.fixed_refresh_sec,
            group_configs.max_items_per_scan,
            group_configs.auto_load_more,
            group_configs.auto_adjust_sort,
            group_configs.enable_desktop_notification,
            group_configs.enable_ntfy,
            group_configs.ntfy_topic,
            group_configs.enable_discord_notification,
            group_configs.discord_webhook
        FROM targets
        JOIN group_configs ON group_configs.group_id = targets.group_id
        WHERE targets.id <> ''
        ON CONFLICT(target_id) DO UPDATE SET
            include_keywords=excluded.include_keywords,
            exclude_keywords=excluded.exclude_keywords,
            min_refresh_sec=excluded.min_refresh_sec,
            max_refresh_sec=excluded.max_refresh_sec,
            jitter_enabled=excluded.jitter_enabled,
            fixed_refresh_sec=excluded.fixed_refresh_sec,
            max_items_per_scan=excluded.max_items_per_scan,
            auto_load_more=excluded.auto_load_more,
            auto_adjust_sort=excluded.auto_adjust_sort,
            enable_desktop_notification=excluded.enable_desktop_notification,
            enable_ntfy=excluded.enable_ntfy,
            ntfy_topic=excluded.ntfy_topic,
            enable_discord_notification=excluded.enable_discord_notification,
            discord_webhook=excluded.discord_webhook
        """
    )
    defaults = PYTHON_TARGET_CONFIG_DEFAULTS
    connection.execute(
        """
        INSERT OR IGNORE INTO target_configs (
            target_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_desktop_notification,
            enable_ntfy, ntfy_topic, enable_discord_notification, discord_webhook
        )
        SELECT id, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        FROM targets
        WHERE id <> ''
        """,
        (
            encode_keywords(()),
            encode_keywords(()),
            defaults.min_refresh_sec,
            defaults.max_refresh_sec,
            int(defaults.jitter_enabled),
            defaults.fixed_refresh_sec,
            defaults.max_items_per_scan,
            int(defaults.auto_load_more),
            int(defaults.auto_adjust_sort),
            int(defaults.enable_desktop_notification),
            int(defaults.enable_ntfy),
            defaults.ntfy_topic,
            int(defaults.enable_discord_notification),
            defaults.discord_webhook,
        ),
    )


def migrate_14_to_15(connection: sqlite3.Connection) -> None:
    """新增排除字忽略片語欄位，既有 target 預設空值。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_configs",
            "exclude_ignore_phrases",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    )


def migrate_15_to_16(connection: sqlite3.Connection) -> None:
    """新增 app-level settings table，保存 theme 等 UI preference。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_16_to_17(connection: sqlite3.Connection) -> None:
    """標記 target scope uniqueness migration；實際 index 建立由 schema 統一收尾。"""

    return None


MIGRATIONS: dict[int, Migration] = {
    10: migrate_10_to_11,
    11: migrate_11_to_12,
    12: migrate_12_to_13,
    13: migrate_13_to_14,
    14: migrate_14_to_15,
    15: migrate_15_to_16,
    16: migrate_16_to_17,
}


def run_known_migrations(connection: sqlite3.Connection, *, from_version: int, to_version: int) -> None:
    """依版本鏈執行已知 migrations，成功後才更新 schema_metadata。"""

    current_version = from_version
    while current_version < to_version:
        migration = MIGRATIONS.get(current_version)
        if migration is None:
            raise RuntimeError(f"Missing SQLite migration {current_version} -> {current_version + 1}")
        migration(connection)
        current_version += 1
        connection.execute(
            """
            INSERT OR REPLACE INTO schema_metadata (key, value)
            VALUES ('version', ?)
            """,
            (str(current_version),),
        )


def add_column_if_missing(
    connection: sqlite3.Connection,
    column: MigrationColumn,
) -> None:
    """在 migration 內補齊舊表缺欄；table 不存在時交給 current schema bootstrap 建立。"""

    if not table_exists(connection, column.table_name):
        return
    rows = connection.execute(f"PRAGMA table_info({column.table_name})").fetchall()
    if any(row[1] == column.column_name for row in rows):
        return
    connection.execute(
        f"ALTER TABLE {column.table_name} ADD COLUMN {column.column_name} {column.definition}"
    )


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """回傳 SQLite table 是否存在。"""

    row = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_legacy_group_configs_table(connection: sqlite3.Connection) -> None:
    """建立舊版 group_configs migration 暫存表；fresh schema 不再使用此表。"""

    connection.execute(
        """
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
            enable_desktop_notification INTEGER NOT NULL DEFAULT 0,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL,
            enable_discord_notification INTEGER NOT NULL DEFAULT 0,
            discord_webhook TEXT NOT NULL DEFAULT ''
        )
        """
    )


__all__ = [
    "MIGRATIONS",
    "Migration",
    "MigrationColumn",
    "V12_TO_13_COLUMNS",
    "add_column_if_missing",
    "migrate_10_to_11",
    "migrate_11_to_12",
    "migrate_12_to_13",
    "migrate_13_to_14",
    "migrate_14_to_15",
    "migrate_15_to_16",
    "migrate_16_to_17",
    "run_known_migrations",
]
