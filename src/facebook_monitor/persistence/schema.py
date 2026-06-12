"""SQLite schema initialization and lightweight migrations。"""

from __future__ import annotations

from functools import lru_cache
import sqlite3

from facebook_monitor.persistence.current_schema import create_current_schema
from facebook_monitor.persistence.current_schema import ensure_dashboard_revision_triggers
from facebook_monitor.persistence.schema_repair import repair_duplicate_target_scopes
from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.persistence.sqlite_codec import write_schema_version

SCHEMA_VERSION = 36
MIN_SUPPORTED_SCHEMA_VERSION = 10
REQUIRED_CURRENT_SCHEMA_TABLES = frozenset(
    {
        "schema_metadata",
        "targets",
        "target_configs",
        "seen_items",
        "target_dedupe_state",
        "logical_items",
        "logical_item_aliases",
        "scan_scope_state",
        "match_history",
        "match_history_matches",
        "latest_scan_items",
        "latest_scan_item_matches",
        "scan_runs",
        "notification_events",
        "notification_dedupe",
        "notification_outbox",
        "target_runtime_state",
        "target_cover_image_refresh_state",
        "global_notification_settings",
        "app_settings",
        "sidebar_groups",
        "sidebar_target_placements",
        "sidebar_group_config_templates",
        "dashboard_revision",
    }
)


def initialize_schema(connection: sqlite3.Connection) -> None:
    """建立目前 SQLite schema。"""

    existing_version = read_supported_schema_version(connection)
    if existing_version == SCHEMA_VERSION:
        validate_current_schema_shape(connection)
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
    if 0 < existing_version < MIN_SUPPORTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported SQLite schema version {existing_version}. "
            f"This app supports automatic migration from version "
            f"{MIN_SUPPORTED_SCHEMA_VERSION}."
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


def validate_current_schema_shape(connection: sqlite3.Connection) -> None:
    """current-version DB 缺正式表或欄位時 fail fast，避免 bootstrap 靜默修補。"""

    existing_tables = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }
    missing_tables = sorted(REQUIRED_CURRENT_SCHEMA_TABLES - existing_tables)
    if missing_tables:
        raise RuntimeError(
            f"SQLite schema version {SCHEMA_VERSION} is missing required table(s): "
            + ", ".join(missing_tables)
        )
    expected_columns = _expected_current_schema_columns()
    missing_columns: list[str] = []
    for table_name in sorted(REQUIRED_CURRENT_SCHEMA_TABLES):
        existing_columns = _table_columns(connection, table_name)
        for column_name in sorted(expected_columns.get(table_name, frozenset())):
            if column_name not in existing_columns:
                missing_columns.append(f"{table_name}.{column_name}")
    if missing_columns:
        raise RuntimeError(
            f"SQLite schema version {SCHEMA_VERSION} is missing required column(s): "
            + ", ".join(missing_columns)
        )
    missing_constraints = _missing_current_schema_constraints(connection)
    if missing_constraints:
        raise RuntimeError(
            f"SQLite schema version {SCHEMA_VERSION} is missing required "
            "constraint(s): "
            + ", ".join(missing_constraints)
        )


@lru_cache(maxsize=1)
def _expected_current_schema_columns() -> dict[str, frozenset[str]]:
    """從正式 current schema 建立欄位基準，避免手寫清單與 SQL 漂移。"""

    expected = sqlite3.connect(":memory:")
    try:
        create_current_schema(expected)
        return {
            table_name: _table_columns(expected, table_name)
            for table_name in REQUIRED_CURRENT_SCHEMA_TABLES
        }
    finally:
        expected.close()


def _table_columns(connection: sqlite3.Connection, table_name: str) -> frozenset[str]:
    """讀取指定 table 目前欄位名稱集合。"""

    return frozenset(
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    )


def _missing_current_schema_constraints(connection: sqlite3.Connection) -> list[str]:
    """檢查 current DB 是否保留必要 CHECK constraints。"""

    targets_sql = _table_sql(connection, "targets")
    required_target_constraints = (
        "CHECK (target_kind IN ('posts', 'comments'))",
        "CHECK (metadata_status IN ('resolved', 'pending', 'failed'))",
        "CHECK (enabled IN (0, 1))",
        "CHECK (paused IN (0, 1))",
        "CHECK (worker_mode IN ('headless', 'headed_compat'))",
    )
    return [
        f"targets.{index}"
        for index, constraint in enumerate(required_target_constraints, start=1)
        if constraint not in targets_sql
    ]


def _table_sql(connection: sqlite3.Connection, table_name: str) -> str:
    """讀取 sqlite_master 中的 table SQL。"""

    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0])


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

