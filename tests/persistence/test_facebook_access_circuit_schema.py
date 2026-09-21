"""Facebook access legacy schema migration 與 v46 移除契約測試。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from facebook_monitor.persistence.migrations import migrate_39_to_40
from facebook_monitor.persistence.migrations import migrate_41_to_42
from facebook_monitor.persistence.migrations import migrate_42_to_43
from facebook_monitor.persistence.migrations import migrate_43_to_44
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
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
        state_sql = _normalized_sql(
            table_sql(connection, "facebook_access_circuit_state")
        )
        events_sql = _normalized_sql(
            table_sql(connection, "facebook_access_circuit_events")
        )

    assert "check(statein('closed','open','half_open'))" in state_sql
    assert "referencesfacebook_access_circuit_state" in events_sql


def test_v39_schema_migrates_through_v46_and_rerun_is_safe(tmp_path: Path) -> None:
    """v39 依序完成歷史 migration，v46 移除退役表且可安全重跑。"""

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
        dashboard_triggers = {
            (str(row["tbl_name"]), str(row["name"]))
            for row in connection.execute(
                """
                SELECT tbl_name, name
                FROM sqlite_master
                WHERE type = 'trigger'
                  AND name LIKE 'trg_dashboard_revision_%'
                """
            ).fetchall()
        }

        assert version == str(SCHEMA_VERSION)
        assert SCHEMA_VERSION == 46
        assert table_exists(connection, "facebook_temporary_block_warning")
        retired_tables = {
            "facebook_access_circuit_state",
            "facebook_access_circuit_events",
            "facebook_automation_pacing_state",
            "managed_profile_identity_binding",
            "facebook_session_recovery_state",
        }
        assert not {
            table_name for table_name in retired_tables if table_exists(connection, table_name)
        }
        assert not {
            "idx_facebook_access_events_profile_occurred",
            "idx_facebook_access_events_episode",
            "idx_facebook_session_recovery_status_lease",
        } & indexes
        assert not {table for table, _name in dashboard_triggers} & retired_tables


def test_v43_to_v44_converts_temporary_block_lock_to_warning(tmp_path: Path) -> None:
    """舊版 temporary-block open state 會停止 active target 並改為 closed warning。"""

    db_path = tmp_path / "temporary-block-migration.db"
    with SqliteApplicationContext(db_path) as app:
        migrate_39_to_40(app.repositories.targets.connection)
        migrate_41_to_42(app.repositories.targets.connection)
        migrate_42_to_43(app.repositories.targets.connection)
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="migration-group",
                canonical_url="https://www.facebook.com/groups/migration-group",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        paused_target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="migration-paused-group",
                canonical_url=(
                    "https://www.facebook.com/groups/migration-paused-group"
                ),
            )
        )
        app.services.targets.restart_target_monitoring(paused_target.id)
        app.services.targets.pause_target_monitoring(paused_target.id)
        app.repositories.targets.connection.execute(
            """
            UPDATE target_runtime_state
            SET runtime_status = 'error',
                last_finished_at = '2026-09-19T23:00:00+00:00',
                last_error = 'existing-paused-diagnostic',
                consecutive_failure_reason = 'existing_reason',
                consecutive_failure_count = 3,
                updated_at = '2026-09-19T23:00:00+00:00'
            WHERE target_id = ?
            """,
            (paused_target.id,),
        )
        app.repositories.targets.connection.execute(
            """
            INSERT INTO facebook_access_circuit_state (
                profile_scope_key, state, episode_id, generation, reason_code,
                source_kind, operation_kind, trigger_action_kind,
                recovery_recipe_kind, trigger_target_id, opened_at,
                last_detected_at, cooldown_until, detection_count, updated_at
            ) VALUES (
                'migration-profile', 'open', 'migration-episode', 1,
                'facebook_temporary_block', 'scan', 'posts_access',
                'group_feed_document', 'group_feed_document_guard_v1', ?,
                '2026-09-20T01:00:00+00:00',
                '2026-09-20T02:00:00+00:00',
                '2026-09-21T02:00:00+00:00', 1,
                '2026-09-20T02:00:00+00:00'
            )
            """,
            (target.id,),
        )
        app.repositories.targets.connection.execute(
            "UPDATE schema_metadata SET value = '43' WHERE key = 'version'"
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        migrated_target = connection.execute(
            "SELECT paused FROM targets WHERE id = ?",
            (target.id,),
        ).fetchone()
        runtime = connection.execute(
            """
            SELECT desired_state, runtime_status, active_worker_id, active_page_id
            FROM target_runtime_state
            WHERE target_id = ?
            """,
            (target.id,),
        ).fetchone()
        paused_runtime = connection.execute(
            """
            SELECT desired_state, runtime_status, last_finished_at, last_error,
                   consecutive_failure_reason, consecutive_failure_count,
                   updated_at
            FROM target_runtime_state
            WHERE target_id = ?
            """,
            (paused_target.id,),
        ).fetchone()
        warning = connection.execute(
            "SELECT * FROM facebook_temporary_block_warning WHERE id = 1"
        ).fetchone()
        assert connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"] == "46"
        assert migrated_target is not None
        assert int(migrated_target["paused"]) == 1
        assert runtime is not None
        assert dict(runtime) == {
            "desired_state": "stopped",
            "runtime_status": "idle",
            "active_worker_id": "",
            "active_page_id": "",
        }
        assert paused_runtime is not None
        assert dict(paused_runtime) == {
            "desired_state": "stopped",
            "runtime_status": "error",
            "last_finished_at": "2026-09-19T23:00:00+00:00",
            "last_error": "existing-paused-diagnostic",
            "consecutive_failure_reason": "existing_reason",
            "consecutive_failure_count": 3,
            "updated_at": "2026-09-19T23:00:00+00:00",
        }
        assert warning is not None
        assert warning["generation"] == 3
        assert warning["detected_at"] == "2026-09-20T02:00:00+00:00"
        assert warning["warning_until"] == "2026-09-20T14:00:00+00:00"
        assert not table_exists(connection, "facebook_access_circuit_state")
        assert not table_exists(connection, "facebook_access_circuit_events")


def test_v43_to_v44_preserves_ambiguous_blocked_session_hold(tmp_path: Path) -> None:
    """舊 blocked row 沒有清理證據，migration 不得猜測完整性已恢復。"""

    db_path = tmp_path / "blocked-session-migration.db"
    with SqliteApplicationContext(db_path) as app:
        migrate_39_to_40(app.repositories.targets.connection)
        migrate_41_to_42(app.repositories.targets.connection)
        migrate_42_to_43(app.repositories.targets.connection)
        app.repositories.targets.connection.execute(
            """
            INSERT INTO facebook_session_recovery_state (
                profile_scope_key, generation, status, marker_session_id,
                stale_detected_at, earliest_probe_at, last_probe_finished_at,
                last_probe_result, updated_at
            ) VALUES (?, 3, 'hold', ?, ?, ?, ?, 'blocked', ?)
            """,
            (
                "blocked-session-profile",
                "11111111-1111-4111-8111-111111111111",
                "2026-09-20T01:00:00+00:00",
                "2026-09-20T02:30:00+00:00",
                "2026-09-20T02:00:00+00:00",
                "2026-09-20T02:00:00+00:00",
            ),
        )
        migrate_43_to_44(app.repositories.targets.connection)
        state = app.repositories.targets.connection.execute(
            """
            SELECT generation, status, last_probe_result, recovered_at
            FROM facebook_session_recovery_state
            WHERE profile_scope_key = 'blocked-session-profile'
            """
        ).fetchone()

    assert state is not None
    assert dict(state) == {
        "generation": 3,
        "status": "hold",
        "last_probe_result": "blocked",
        "recovered_at": "",
    }


def test_v41_to_v42_identity_binding_migration_remains_idempotent(
    tmp_path: Path,
) -> None:
    """已發布的 identity binding migration 仍可獨立重跑。"""

    db_path = tmp_path / "identity-migration.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        migrate_41_to_42(connection)
        migrate_41_to_42(connection)
        migrated_sql = _normalized_sql(
            table_sql(connection, "managed_profile_identity_binding")
        )
    assert "check(length(marker_uuid)=36)" in migrated_sql


def test_v42_to_v43_session_recovery_migration_remains_idempotent(
    tmp_path: Path,
) -> None:
    """已發布的 session recovery migration 仍可獨立重跑。"""

    db_path = tmp_path / "session-recovery-migration.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")
        migrate_42_to_43(connection)
        migrate_42_to_43(connection)
        migrated_sql = _normalized_sql(
            table_sql(connection, "facebook_session_recovery_state")
        )
        indexes = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert "check(generation>=1)" in migrated_sql
    assert "idx_facebook_session_recovery_status_lease" in indexes


def test_legacy_session_recovery_schema_rejects_incomplete_probe_owner(
    tmp_path: Path,
) -> None:
    """Legacy probing row 缺 request/token/lease 時仍由 DDL 拒絕。"""

    db_path = tmp_path / "session-recovery-check.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")
        migrate_42_to_43(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO facebook_session_recovery_state (
                    profile_scope_key, status, marker_session_id,
                    stale_detected_at, earliest_probe_at, updated_at
                ) VALUES (?, 'probing', ?, ?, ?, ?)
                """,
                (
                    "scope",
                    "11111111-1111-4111-8111-111111111111",
                    "2026-09-19T01:02:03+00:00",
                    "2026-09-19T01:02:03+00:00",
                    "2026-09-19T01:02:03+00:00",
                ),
            )


def test_legacy_circuit_schema_enforces_cross_field_checks(tmp_path: Path) -> None:
    """已發布的 circuit migration 仍保留 cross-field 契約。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")
        migrate_39_to_40(connection)

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
