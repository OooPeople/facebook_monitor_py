"""SQLite migration chain。

職責：保存明確版本鏈的 migration entrypoint。既有 DB 欄位補齊必須進
本模組的版本鏈，不得另建 current-schema repair 平行路徑。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
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
    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_configs",
            "exclude_ignore_phrases",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    )
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
    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_configs",
            "exclude_ignore_phrases",
            "TEXT NOT NULL DEFAULT '[]'",
        ),
    )
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
            exclude_ignore_phrases='[]',
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


def migrate_17_to_18(connection: sqlite3.Connection) -> None:
    """新增 target metadata refresh 狀態欄位。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "targets",
            "metadata_status",
            "TEXT NOT NULL DEFAULT 'resolved'",
        ),
    )
    add_column_if_missing(
        connection,
        MigrationColumn(
            "targets",
            "metadata_error",
            "TEXT NOT NULL DEFAULT ''",
        ),
    )


def migrate_18_to_19(connection: sqlite3.Connection) -> None:
    """新增 sidebar layout tables，並為既有 targets 建立未分組 placement。"""

    connection.executescript(
        """
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

        CREATE INDEX IF NOT EXISTS idx_sidebar_groups_order
            ON sidebar_groups(sort_order);
        CREATE INDEX IF NOT EXISTS idx_sidebar_target_placements_group_order
            ON sidebar_target_placements(sidebar_group_id, sort_order);
        """
    )
    target_rows = connection.execute(
        """
        SELECT id
        FROM targets
        ORDER BY created_at, id
        """
    ).fetchall()
    for index, row in enumerate(target_rows):
        connection.execute(
            """
            INSERT OR IGNORE INTO sidebar_target_placements (
                target_id, sidebar_group_id, sort_order, updated_at
            )
            VALUES (?, NULL, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (row["id"], index),
        )


def migrate_19_to_20(connection: sqlite3.Connection) -> None:
    """新增 sidebar group config template table。"""

    connection.execute(
        """
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
        )
        """
    )


def migrate_20_to_21(connection: sqlite3.Connection) -> None:
    """新增多 keyword 命中正規化表，並回填既有摘要欄位。"""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS match_history_matches (
            history_id INTEGER NOT NULL REFERENCES match_history(id) ON DELETE CASCADE,
            match_order INTEGER NOT NULL,
            rule TEXT NOT NULL,
            PRIMARY KEY (history_id, match_order)
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

        CREATE INDEX IF NOT EXISTS idx_match_history_matches_history
            ON match_history_matches(history_id, match_order);
        CREATE INDEX IF NOT EXISTS idx_latest_scan_item_matches_target_item
            ON latest_scan_item_matches(target_id, item_key, match_order);
        """
    )
    for row in connection.execute(
        """
        SELECT id, include_rule
        FROM match_history
        WHERE include_rule <> ''
        """
    ).fetchall():
        for index, rule in enumerate(split_keyword_rule_text(row["include_rule"])):
            connection.execute(
                """
                INSERT OR IGNORE INTO match_history_matches (history_id, match_order, rule)
                VALUES (?, ?, ?)
                """,
                (row["id"], index, rule),
            )
    for row in connection.execute(
        """
        SELECT target_id, item_key, matched_keyword
        FROM latest_scan_items
        WHERE matched_keyword <> ''
        """
    ).fetchall():
        for index, rule in enumerate(split_keyword_rule_text(row["matched_keyword"])):
            connection.execute(
                """
                INSERT OR IGNORE INTO latest_scan_item_matches (
                    target_id, item_key, match_order, rule
                )
                VALUES (?, ?, ?, ?)
                """,
                (row["target_id"], row["item_key"], index, rule),
            )


def migrate_21_to_22(connection: sqlite3.Connection) -> None:
    """新增 dashboard 熱查詢使用的 id 排序索引。"""

    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_id_desc
            ON scan_runs(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_runs_target_status_id_desc
            ON scan_runs(target_id, status, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_id_desc
            ON notification_events(target_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_notification_events_target_channel_id_desc
            ON notification_events(target_id, channel, id DESC);
        """
    )


def migrate_22_to_23(connection: sqlite3.Connection) -> None:
    """新增 target 社團封面圖 URL metadata 欄位。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "targets",
            "group_cover_image_url",
            "TEXT NOT NULL DEFAULT ''",
        ),
    )


def migrate_23_to_24(connection: sqlite3.Connection) -> None:
    """新增 scan scope baseline state，供非使用者 start 的安全清理使用。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_scope_state (
            scope_id TEXT PRIMARY KEY,
            initialized INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migrate_24_to_25(connection: sqlite3.Connection) -> None:
    """新增 UI 顯示用 next due 欄位；scheduler 不以此欄位作排程判斷。"""

    add_column_if_missing(
        connection,
        MigrationColumn(
            "target_runtime_state",
            "display_next_due_at",
            "TEXT NOT NULL DEFAULT ''",
        ),
    )


MIGRATIONS: dict[int, Migration] = {
    10: migrate_10_to_11,
    11: migrate_11_to_12,
    12: migrate_12_to_13,
    13: migrate_13_to_14,
    14: migrate_14_to_15,
    15: migrate_15_to_16,
    16: migrate_16_to_17,
    17: migrate_17_to_18,
    18: migrate_18_to_19,
    19: migrate_19_to_20,
    20: migrate_20_to_21,
    21: migrate_21_to_22,
    22: migrate_22_to_23,
    23: migrate_23_to_24,
    24: migrate_24_to_25,
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
    "migrate_17_to_18",
    "migrate_18_to_19",
    "migrate_19_to_20",
    "migrate_20_to_21",
    "migrate_21_to_22",
    "migrate_22_to_23",
    "migrate_23_to_24",
    "migrate_24_to_25",
    "run_known_migrations",
]
