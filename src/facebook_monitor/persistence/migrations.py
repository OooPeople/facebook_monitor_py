"""SQLite migration chain。

職責：保存目前仍支援的明確版本鏈。既有 DB 欄位補齊必須進本模組的
版本鏈，不得另建 current-schema repair 平行路徑。
"""

from __future__ import annotations

from collections.abc import Callable
import sqlite3


Migration = Callable[[sqlite3.Connection], None]


TARGETS_V35_TO_V36_COLUMNS = (
    "id",
    "name",
    "target_kind",
    "group_id",
    "group_name",
    "group_cover_image_url",
    "parent_post_id",
    "scope_id",
    "canonical_url",
    "metadata_status",
    "metadata_error",
    "enabled",
    "paused",
    "worker_mode",
    "created_at",
    "updated_at",
)


TARGETS_V35_TO_V36_CREATE_SQL_TEMPLATE = """
CREATE TABLE {table_name} (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('posts', 'comments')),
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_cover_image_url TEXT NOT NULL DEFAULT '',
    parent_post_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    metadata_status TEXT NOT NULL DEFAULT 'resolved'
        CHECK (metadata_status IN ('resolved', 'pending', 'failed')),
    metadata_error TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    paused INTEGER NOT NULL CHECK (paused IN (0, 1)),
    worker_mode TEXT NOT NULL CHECK (worker_mode IN ('headless', 'headed_compat')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


TARGETS_V36_ENUM_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("target_kind", ("posts", "comments")),
    ("metadata_status", ("resolved", "pending", "failed")),
    ("worker_mode", ("headless", "headed_compat")),
)


TARGETS_V36_BOOLEAN_CHECKS = ("enabled", "paused")


def migrate_35_to_36(connection: sqlite3.Connection) -> None:
    """重建 targets table，導入核心 enum / boolean CHECK constraints。"""

    rebuild_targets_table_with_check_constraints(connection)


def migrate_36_to_37(connection: sqlite3.Connection) -> None:
    """移除不再是正式設定來源的舊全域通知設定表。"""

    connection.execute("DROP TABLE IF EXISTS global_notification_settings")


def migrate_37_to_38(connection: sqlite3.Connection) -> None:
    """將 match_history 記錄時間欄位改為符合產品語義的 recorded_at。"""

    if not table_exists(connection, "match_history"):
        return
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(match_history)").fetchall()
    }
    if "recorded_at" in columns:
        return
    if "notified_at" not in columns:
        return
    connection.execute("ALTER TABLE match_history RENAME COLUMN notified_at TO recorded_at")


MIGRATIONS: dict[int, Migration] = {
    35: migrate_35_to_36,
    36: migrate_36_to_37,
    37: migrate_37_to_38,
}


def run_known_migrations(
    connection: sqlite3.Connection,
    *,
    from_version: int,
    to_version: int,
) -> None:
    """依版本鏈執行已知 migrations，成功後才更新 schema_metadata。"""

    current_version = from_version
    while current_version < to_version:
        migration = MIGRATIONS.get(current_version)
        if migration is None:
            raise RuntimeError(
                f"Missing SQLite migration {current_version} -> {current_version + 1}"
            )
        migration(connection)
        current_version += 1
        connection.execute(
            """
            INSERT OR REPLACE INTO schema_metadata (key, value)
            VALUES ('version', ?)
            """,
            (str(current_version),),
        )


def rebuild_targets_table_with_check_constraints(connection: sqlite3.Connection) -> None:
    """以 parent-table-safe rebuild 將 targets 核心語義升成 DB CHECK。"""

    if not table_exists(connection, "targets"):
        return
    violations = _targets_v36_check_violations(connection)
    if violations:
        raise RuntimeError(
            "SQLite targets table contains values incompatible with v36 CHECK "
            "constraints: "
            + "; ".join(violations)
        )

    temp_table = "__targets_v36_checked"
    old_foreign_keys_enabled = _foreign_keys_enabled(connection)
    if connection.in_transaction:
        connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    if _foreign_keys_enabled(connection):
        raise RuntimeError("SQLite failed to disable foreign keys for targets rebuild")
    try:
        connection.execute(f"DROP TABLE IF EXISTS {temp_table}")
        connection.execute(
            TARGETS_V35_TO_V36_CREATE_SQL_TEMPLATE.format(table_name=temp_table)
        )
        columns_sql = ", ".join(TARGETS_V35_TO_V36_COLUMNS)
        old_count = _table_row_count(connection, "targets")
        connection.execute(
            f"""
            INSERT INTO {temp_table} ({columns_sql})
            SELECT {columns_sql}
            FROM targets
            """
        )
        new_count = _table_row_count(connection, temp_table)
        if new_count != old_count:
            raise RuntimeError(
                "SQLite targets rebuild copied an unexpected row count: "
                f"old={old_count}, new={new_count}"
            )
        connection.execute("DROP TABLE targets")
        connection.execute(f"ALTER TABLE {temp_table} RENAME TO targets")
        _raise_for_foreign_key_check_failures(connection)
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.execute(
            f"PRAGMA foreign_keys = {'ON' if old_foreign_keys_enabled else 'OFF'}"
        )


def _targets_v36_check_violations(connection: sqlite3.Connection) -> tuple[str, ...]:
    """回傳 targets v36 CHECK preflight 發現的違規欄位摘要。"""

    violations: list[str] = []
    for field, allowed_values in TARGETS_V36_ENUM_CHECKS:
        placeholders = ", ".join("?" for _ in allowed_values)
        rows = connection.execute(
            f"""
            SELECT id
            FROM targets
            WHERE {field} NOT IN ({placeholders})
               OR {field} IS NULL
            ORDER BY id
            LIMIT 5
            """,
            allowed_values,
        ).fetchall()
        if rows:
            violations.append(_target_violation_summary(field, rows))
    for field in TARGETS_V36_BOOLEAN_CHECKS:
        rows = connection.execute(
            f"""
            SELECT id
            FROM targets
            WHERE {field} NOT IN (0, 1)
               OR {field} IS NULL
            ORDER BY id
            LIMIT 5
            """
        ).fetchall()
        if rows:
            violations.append(_target_violation_summary(field, rows))
    return tuple(violations)


def _target_violation_summary(field: str, rows: list[sqlite3.Row]) -> str:
    """將 target CHECK 違規列成短摘要，避免 migration 只回模糊 IntegrityError。"""

    row_ids = ", ".join(str(_row_first_value(row)) for row in rows)
    return f"targets.{field} invalid row id(s): {row_ids}"


def _row_first_value(row: sqlite3.Row) -> object:
    """讀取 sqlite Row / tuple 的第一個欄位值。"""

    try:
        return row[0]
    except (IndexError, TypeError):
        return ""


def _table_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    """回傳 migration table row count。"""

    row = connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0] if row is not None else 0)


def _foreign_keys_enabled(connection: sqlite3.Connection) -> bool:
    """回傳目前 SQLite foreign_keys pragma 是否啟用。"""

    row = connection.execute("PRAGMA foreign_keys").fetchone()
    return bool(row[0] if row is not None else 0)


def _raise_for_foreign_key_check_failures(connection: sqlite3.Connection) -> None:
    """確認 parent-table rebuild 沒留下 foreign key violation。"""

    rows = connection.execute("PRAGMA foreign_key_check").fetchmany(5)
    if rows:
        details = ", ".join(
            f"{row[0]} rowid={row[1]} parent={row[2]} fk={row[3]}" for row in rows
        )
        raise RuntimeError(
            f"SQLite foreign_key_check failed after targets rebuild: {details}"
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


__all__ = [
    "MIGRATIONS",
    "Migration",
    "TARGETS_V35_TO_V36_COLUMNS",
    "migrate_35_to_36",
    "migrate_36_to_37",
    "migrate_37_to_38",
    "rebuild_targets_table_with_check_constraints",
    "run_known_migrations",
    "table_exists",
]
