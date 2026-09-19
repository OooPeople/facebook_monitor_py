"""Facebook access circuit schema v40 tests。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from facebook_monitor.persistence.current_schema import create_current_schema
from facebook_monitor.persistence.migrations import migrate_39_to_40
from facebook_monitor.persistence.migrations import migrate_41_to_42
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from tests.persistence.sqlite_test_helpers import table_exists
from tests.persistence.sqlite_test_helpers import table_sql


def test_v39_to_v40_migration_function_is_idempotent(tmp_path: Path) -> None:
    """Migration 本身可在只有 v39 parent table 的 DB 建表並安全重跑。"""

    db_path = tmp_path / "migration.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")

        migrate_39_to_40(connection)
        migrate_39_to_40(connection)

        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(facebook_access_circuit_state)"
        ).fetchall()
        assert table_exists(connection, "facebook_access_circuit_state")
        assert table_exists(connection, "facebook_access_circuit_events")
        assert {
            (str(row["from"]), str(row["table"]), str(row["on_delete"])) for row in foreign_keys
        } == {
            ("requested_target_id", "targets", "SET NULL"),
            ("trigger_target_id", "targets", "SET NULL"),
        }
        migrated_sql = {
            table_name: _normalized_sql(table_sql(connection, table_name))
            for table_name in (
                "facebook_access_circuit_state",
                "facebook_access_circuit_events",
            )
        }

    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        create_current_schema(expected)
        expected_sql = {
            table_name: _normalized_sql(table_sql(expected, table_name))
            for table_name in migrated_sql
        }
    finally:
        expected.close()

    assert migrated_sql == expected_sql


def test_v39_schema_migrates_through_v43_and_rerun_is_safe(tmp_path: Path) -> None:
    """v39 依序建立 circuit/pacing/identity/recovery state且可重跑。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.executescript(
            """
            CREATE TABLE schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO schema_metadata (key, value) VALUES ('version', '39');
            """
        )
        initialize_schema(connection)
        initialize_schema(connection)

        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        indexes = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        circuit_triggers = {
            (str(row["tbl_name"]), str(row["name"]))
            for row in connection.execute(
                """
                SELECT tbl_name, name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND tbl_name LIKE 'facebook_access_circuit_%'
                """
            ).fetchall()
        }

        assert version == str(SCHEMA_VERSION)
        assert SCHEMA_VERSION == 43
        assert table_exists(connection, "managed_profile_identity_binding")
        assert table_exists(connection, "facebook_session_recovery_state")
        assert table_exists(connection, "facebook_automation_pacing_state")
        assert table_exists(connection, "facebook_access_circuit_state")
        assert table_exists(connection, "facebook_access_circuit_events")
        assert "idx_facebook_access_events_profile_occurred" in indexes
        assert "idx_facebook_access_events_episode" in indexes
        assert (
            len(
                {
                    name
                    for table, name in circuit_triggers
                    if table == "facebook_access_circuit_state"
                }
            )
            == 3
        )
        assert not {
            name for table, name in circuit_triggers if table == "facebook_access_circuit_events"
        }


def test_v41_to_v42_identity_binding_migration_matches_current_schema(
    tmp_path: Path,
) -> None:
    """Identity binding migration 可重跑，且 DDL 與 current schema 一致。"""

    db_path = tmp_path / "identity-migration.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        migrate_41_to_42(connection)
        migrate_41_to_42(connection)
        migrated_sql = _normalized_sql(
            table_sql(connection, "managed_profile_identity_binding")
        )

    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        create_current_schema(expected)
        expected_sql = _normalized_sql(
            table_sql(expected, "managed_profile_identity_binding")
        )
    finally:
        expected.close()

    assert migrated_sql == expected_sql


def test_circuit_schema_enforces_cross_field_checks(tmp_path: Path) -> None:
    """Half-open owner、open episode 與 pending request 必須符合 cross-field 契約。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO facebook_access_circuit_state (
                    profile_scope_key, state, updated_at
                ) VALUES ('half-open-without-owner', 'half_open', '2026-01-01T00:00:00Z')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO facebook_access_circuit_state (
                    profile_scope_key, state, episode_id, opened_at, cooldown_until,
                    half_open_token, half_open_started_at,
                    half_open_lease_expires_at, updated_at
                ) VALUES (
                    'invalid-lease', 'half_open', 'episode',
                    '2026-01-01T00:00:00Z', '2026-01-01T12:00:00Z', 'token',
                    '2026-01-01T12:00:00Z', '2026-01-01T12:00:00Z',
                    '2026-01-01T12:00:00Z'
                )
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO facebook_access_circuit_state (
                    profile_scope_key, state, episode_id, opened_at, cooldown_until,
                    probe_request_id, probe_requested_at, requested_recipe_kind, updated_at
                ) VALUES (
                    'early-request', 'open', 'episode', '2026-01-01T00:00:00Z',
                    '2026-01-02T00:00:00Z', 'request', '2026-01-01T12:00:00Z',
                    'group_feed_document_guard_v1', '2026-01-01T12:00:00Z'
                )
                """
            )


def _normalized_sql(value: str) -> str:
    """忽略 migration/current schema 排版，只比較實際 DDL token。"""

    return "".join(value.split()).lower()
