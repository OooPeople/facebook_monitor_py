"""Persistence smoke tests。"""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sqlite3

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import MIN_SUPPORTED_SCHEMA_VERSION
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.schema import initialize_schema


def test_initialize_schema_rejects_existing_db_without_schema_metadata(
    tmp_path: Path,
) -> None:
    """沒有 schema_metadata 的既有 DB 不得被 migration 靜默吞掉。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")

    try:
        with SqliteConnection(db_path) as sqlite:
            initialize_schema(sqlite.require_connection())
    except RuntimeError as exc:
        assert "Unsupported SQLite schema version 0" in str(exc)
    else:
        raise AssertionError("existing DB without schema_metadata should fail fast")


def test_initialize_schema_rejects_existing_db_with_metadata_but_missing_version(
    tmp_path: Path,
) -> None:
    """有 metadata table 但缺 version row 的既有 DB 不得被標成 current。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("CREATE TABLE target_configs (target_id TEXT PRIMARY KEY)")

    try:
        with SqliteConnection(db_path) as sqlite:
            initialize_schema(sqlite.require_connection())
    except RuntimeError as exc:
        assert "valid schema_metadata version" in str(exc)
    else:
        raise AssertionError("existing DB with missing schema version should fail fast")


def test_initialize_schema_rejects_existing_db_with_invalid_schema_version(
    tmp_path: Path,
) -> None:
    """有 metadata table 但 version 無法解析時不得靜默跳過 migration。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO schema_metadata (key, value) VALUES ('version', 'not-an-int')"
        )
        connection.execute("CREATE TABLE target_configs (target_id TEXT PRIMARY KEY)")

    try:
        with SqliteConnection(db_path) as sqlite:
            initialize_schema(sqlite.require_connection())
    except RuntimeError as exc:
        assert "valid schema_metadata version" in str(exc)
    else:
        raise AssertionError("existing DB with invalid schema version should fail fast")


def test_initialize_schema_rejects_too_old_schema_before_creating_current_tables(
    tmp_path: Path,
) -> None:
    """低於支援下限的 schema 不應先建立 current tables 才失敗。"""

    db_path = tmp_path / "app.db"
    too_old_version = MIN_SUPPORTED_SCHEMA_VERSION - 1
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO schema_metadata (key, value) VALUES ('version', ?)",
            (str(too_old_version),),
        )

    try:
        with SqliteConnection(db_path) as sqlite:
            initialize_schema(sqlite.require_connection())
    except RuntimeError as exc:
        assert f"Unsupported SQLite schema version {too_old_version}" in str(exc)
        assert (
            f"automatic migration from version {MIN_SUPPORTED_SCHEMA_VERSION}"
            in str(exc)
        )
    else:
        raise AssertionError("schema version below migration floor should fail fast")

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert table_names == {"schema_metadata"}


def test_initialize_schema_rejects_future_schema_version(tmp_path: Path) -> None:
    """高於目前 app 支援版本的 DB 不得被舊版 app 靜默接受。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute(
            "UPDATE schema_metadata SET value = ? WHERE key = 'version'",
            (str(SCHEMA_VERSION + 1),),
        )

    try:
        with SqliteConnection(db_path) as sqlite:
            initialize_schema(sqlite.require_connection())
    except RuntimeError as exc:
        assert f"Unsupported SQLite schema version {SCHEMA_VERSION + 1}" in str(exc)
        assert f"supports up to version {SCHEMA_VERSION}" in str(exc)
    else:
        raise AssertionError("future schema version should fail fast")


def test_initialize_schema_rejects_current_version_db_missing_required_tables(
    tmp_path: Path,
) -> None:
    """已標成 current version 的 DB 缺正式表時不得被 bootstrap 靜默補完。"""

    db_path = tmp_path / "app.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO schema_metadata (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()

    with SqliteConnection(db_path) as sqlite:
        try:
            initialize_schema(sqlite.require_connection())
        except RuntimeError as exc:
            assert f"SQLite schema version {SCHEMA_VERSION} is missing" in str(exc)
            assert "targets" in str(exc)
        else:
            raise AssertionError("current schema missing required tables should fail fast")


def test_initialize_schema_rejects_current_version_db_missing_required_columns(
    tmp_path: Path,
) -> None:
    """已標成 current version 的 DB 缺正式欄位時不得留到 repository 才爆錯。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP TABLE target_configs")
        connection.execute("CREATE TABLE target_configs (target_id TEXT PRIMARY KEY)")
        connection.commit()

    with SqliteConnection(db_path) as sqlite:
        try:
            initialize_schema(sqlite.require_connection())
        except RuntimeError as exc:
            assert f"SQLite schema version {SCHEMA_VERSION} is missing" in str(exc)
            assert "target_configs.include_keywords" in str(exc)
        else:
            raise AssertionError("current schema missing required columns should fail fast")


def test_initialize_schema_rejects_current_version_db_missing_target_constraints(
    tmp_path: Path,
) -> None:
    """已標成 current version 的 DB 若缺 targets CHECK，不得被視為合法 current。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP TABLE targets")
        connection.execute(
            """
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
            )
            """
        )
        connection.commit()

    with SqliteConnection(db_path) as sqlite:
        try:
            initialize_schema(sqlite.require_connection())
        except RuntimeError as exc:
            assert f"SQLite schema version {SCHEMA_VERSION} is missing" in str(exc)
            assert "constraint" in str(exc)
            assert "targets." in str(exc)
        else:
            raise AssertionError("current schema missing targets CHECK should fail fast")


def test_initialize_schema_rejects_current_version_duplicate_target_scopes(
    tmp_path: Path,
) -> None:
    """current DB 若出現 duplicate scope，bootstrap 不可靜默合併資料。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP INDEX idx_targets_kind_scope_unique")
        first = TargetDescriptor.for_group_posts(
            group_id="duplicate-current",
            canonical_url="https://www.facebook.com/groups/duplicate-current",
        )
        duplicate = replace(
            first,
            id="duplicate-current-copy",
            created_at=first.created_at + timedelta(seconds=1),
            updated_at=first.updated_at + timedelta(seconds=1),
        )
        repository = TargetRepository(connection)
        repository.save(first)
        repository.save(duplicate)

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        try:
            initialize_schema(connection)
        except RuntimeError as exc:
            assert "duplicate target scopes" in str(exc)
            assert "duplicate-current-copy" in str(exc)
        else:
            raise AssertionError("current duplicate target scopes should fail fast")
        rows = connection.execute(
            "SELECT id FROM targets WHERE scope_id = ? ORDER BY created_at, id",
            (first.scope_id,),
        ).fetchall()

    assert [row["id"] for row in rows] == [first.id, duplicate.id]


def test_initialize_schema_accepts_plain_sqlite_connection_for_current_db(
    tmp_path: Path,
) -> None:
    """read_schema_version 不應隱含依賴 sqlite3.Row row factory。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        initialize_schema(sqlite.require_connection())

    with closing(sqlite3.connect(db_path)) as connection:
        initialize_schema(connection)

        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()

    assert row is not None
    assert row[0] == str(SCHEMA_VERSION)
