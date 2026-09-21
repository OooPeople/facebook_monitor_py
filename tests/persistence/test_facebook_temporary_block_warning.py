"""Schema v46 temporary-block warning persistence tests。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.persistence.current_schema import create_current_schema
from facebook_monitor.persistence.migrations import migrate_39_to_40
from facebook_monitor.persistence.migrations import migrate_44_to_45
from facebook_monitor.persistence.repositories.facebook_temporary_block_warning import (
    TemporaryBlockWarningDecodeError,
)
from facebook_monitor.webapp.dashboard_revision_query import get_dashboard_revision
from tests.persistence.sqlite_test_helpers import table_sql


_NOW = datetime(2026, 8, 1, 3, 4, 5, tzinfo=UTC)


def test_repository_records_singleton_generation_and_fixed_warning_window(
    tmp_path: Path,
) -> None:
    """每次 confirmed finding 推進同一 row generation 並重設十二小時期限。"""

    db_path = tmp_path / "app.db"
    finding = TemporaryBlockFinding(
        source_kind=FacebookWorkSourceKind.SCAN,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
    )
    with SqliteApplicationContext(db_path):
        pass
    revision_before = int(get_dashboard_revision(db_path).revision)

    with SqliteApplicationContext(db_path) as app:
        first = app.services.facebook_temporary_block_warning.record(
            finding,
            detected_at=_NOW,
        )
        second = app.services.facebook_temporary_block_warning.record(
            finding,
            detected_at=_NOW + timedelta(minutes=5),
        )
        row_count = app.repositories.targets.connection.execute(
            "SELECT COUNT(1) AS count FROM facebook_temporary_block_warning"
        ).fetchone()["count"]
    revision_after = int(get_dashboard_revision(db_path).revision)

    assert first.generation == 1
    assert first.warning_until == _NOW + timedelta(hours=12)
    assert second.generation == 2
    assert second.warning_until == _NOW + timedelta(hours=12, minutes=5)
    assert row_count == 1
    assert revision_after >= revision_before + 2


@pytest.mark.parametrize(
    ("field", "corrupt_value", "expected_field"),
    [
        ("generation", "raw-generation", "generation"),
        ("generation", 0, "generation"),
        ("detected_at", "raw-detected-at", "detected_at"),
        ("detected_at", "2026-08-01T03:04:05+08:00", "detected_at"),
        ("updated_at", "raw-updated-at", "updated_at"),
        ("source_kind", "raw-source", "source_kind"),
        ("operation_kind", "raw-operation", "operation_kind"),
        ("action_kind", "raw-action", "action_kind"),
        ("warning_until", _NOW.isoformat(), "warning_window"),
    ],
)
def test_repository_rejects_corrupt_warning_row_without_echoing_raw_value(
    tmp_path: Path,
    field: str,
    corrupt_value: object,
    expected_field: str,
) -> None:
    """Warning decoder 應回傳安全欄位錯誤，不洩漏 durable row 原值。"""

    db_path = tmp_path / "app.db"
    finding = TemporaryBlockFinding(
        source_kind=FacebookWorkSourceKind.SCAN,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
    )
    with SqliteApplicationContext(db_path) as app:
        app.services.facebook_temporary_block_warning.record(
            finding,
            detected_at=_NOW,
        )
        connection = app.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE facebook_temporary_block_warning SET {field} = ? WHERE id = 1",
            (corrupt_value,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

        with pytest.raises(TemporaryBlockWarningDecodeError) as error:
            app.services.facebook_temporary_block_warning.get()

    assert error.value.field == expected_field
    assert str(corrupt_value) not in str(error.value)


def test_v46_warning_schema_rejects_probe_source(tmp_path: Path) -> None:
    """Fresh v46 schema 不得承載已移除的 recovery probe source。"""

    with SqliteApplicationContext(tmp_path / "app.db") as app:
        connection = app.repositories.targets.connection
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO facebook_temporary_block_warning (
                    id, generation, detected_at, warning_until,
                    source_kind, operation_kind, action_kind, updated_at
                ) VALUES (1, 1, ?, ?, 'probe', 'comments_access',
                          'trusted_click', ?)
                """,
                (
                    _NOW.isoformat(),
                    (_NOW + timedelta(hours=12)).isoformat(),
                    _NOW.isoformat(),
                ),
            )


def test_v44_migration_table_matches_current_schema() -> None:
    """歷史 migration 與 fresh v46 schema 的 warning DDL 必須一致。"""

    migrated = sqlite3.connect(":memory:")
    migrated.row_factory = sqlite3.Row
    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        create_current_schema(migrated)
        migrated.execute("DROP TABLE facebook_temporary_block_warning")
        migrate_39_to_40(migrated)
        migrate_44_to_45(migrated)
        create_current_schema(expected)

        migrated_sql = _normalized_sql(
            table_sql(migrated, "facebook_temporary_block_warning")
        )
        expected_sql = _normalized_sql(
            table_sql(expected, "facebook_temporary_block_warning")
        )
    finally:
        migrated.close()
        expected.close()

    assert migrated_sql == expected_sql


def test_v44_to_v45_backfill_is_deterministic_and_never_pauses_targets(
    tmp_path: Path,
) -> None:
    """Backfill選最新 detection、使舊generation失效，且不重演pause-all。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        app.services.targets.restart_target_monitoring(active.id)
        pre_paused = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="pre-paused",
                canonical_url="https://www.facebook.com/groups/pre-paused",
            )
        )
        app.services.targets.pause_target_monitoring(pre_paused.id)
        connection = app.repositories.targets.connection
        connection.execute("DELETE FROM facebook_temporary_block_warning")
        migrate_39_to_40(connection)
        _insert_legacy_warning(
            connection,
            scope="older-scope",
            generation=9,
            detected_at=_NOW - timedelta(hours=2),
            source="scan",
            operation="posts_access",
            action="group_feed_document",
        )
        _insert_legacy_warning(
            connection,
            scope="newer-scope",
            generation=3,
            detected_at=_NOW,
            source="metadata",
            operation="group_metadata_access",
            action="group_document",
        )

        migrate_44_to_45(connection)
        migrate_44_to_45(connection)

        row = connection.execute(
            "SELECT * FROM facebook_temporary_block_warning WHERE id = 1"
        ).fetchone()
        active_after = app.repositories.targets.get(active.id)
        paused_after = app.repositories.targets.get(pre_paused.id)

    assert row["generation"] == 10
    assert row["detected_at"] == _NOW.isoformat()
    assert row["warning_until"] == (_NOW + timedelta(hours=12)).isoformat()
    assert row["source_kind"] == "metadata"
    assert row["operation_kind"] == "group_metadata_access"
    assert row["action_kind"] == "group_document"
    assert active_after is not None and not active_after.paused
    assert paused_after is not None and paused_after.paused


def test_v44_to_v45_ignores_non_block_and_invalid_time_rows(tmp_path: Path) -> None:
    """非 confirmed block 或無可信時間的 legacy state 不得冒充 warning。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        connection.execute("DELETE FROM facebook_temporary_block_warning")
        migrate_39_to_40(connection)
        _insert_legacy_warning(
            connection,
            scope="invalid-time",
            generation=4,
            detected_at=None,
            source="scan",
            operation="posts_access",
            action="group_feed_document",
        )
        connection.execute(
            """
            INSERT INTO facebook_access_circuit_state (
                profile_scope_key, state, generation, reason_code, updated_at
            ) VALUES (?, 'closed', 8, 'facebook_access_persistence_uncertain', ?)
            """,
            ("integrity-hold", _NOW.isoformat()),
        )

        migrate_44_to_45(connection)

        row = connection.execute(
            "SELECT * FROM facebook_temporary_block_warning WHERE id = 1"
        ).fetchone()

    assert row is None


def test_v44_to_v45_does_not_backfill_probe_only_warning(tmp_path: Path) -> None:
    """Legacy manual/session probe 不得成為現行 advisory warning evidence。"""

    db_path = tmp_path / "probe-only.db"
    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        connection.execute("DELETE FROM facebook_temporary_block_warning")
        migrate_39_to_40(connection)
        _insert_legacy_warning(
            connection,
            scope="probe-only",
            generation=99,
            detected_at=_NOW,
            source="probe",
            operation="comments_access",
            action="trusted_click",
        )

        migrate_44_to_45(connection)

        row = connection.execute(
            "SELECT * FROM facebook_temporary_block_warning WHERE id = 1"
        ).fetchone()

    assert row is None


def test_v44_to_v45_probe_generation_does_not_advance_formal_warning(
    tmp_path: Path,
) -> None:
    """Probe 的高 generation 不得污染正式 runtime evidence 的 generation fence。"""

    db_path = tmp_path / "probe-generation.db"
    with SqliteApplicationContext(db_path) as app:
        connection = app.repositories.targets.connection
        connection.execute("DELETE FROM facebook_temporary_block_warning")
        migrate_39_to_40(connection)
        _insert_legacy_warning(
            connection,
            scope="formal-scan",
            generation=4,
            detected_at=_NOW - timedelta(hours=1),
            source="scan",
            operation="posts_access",
            action="group_feed_document",
        )
        _insert_legacy_warning(
            connection,
            scope="probe-high-generation",
            generation=999,
            detected_at=_NOW,
            source="probe",
            operation="comments_access",
            action="trusted_click",
        )

        migrate_44_to_45(connection)

        row = connection.execute(
            "SELECT * FROM facebook_temporary_block_warning WHERE id = 1"
        ).fetchone()

    assert row is not None
    assert row["generation"] == 5
    assert row["detected_at"] == (_NOW - timedelta(hours=1)).isoformat()
    assert row["source_kind"] == "scan"


def _insert_legacy_warning(
    connection: sqlite3.Connection,
    *,
    scope: str,
    generation: int,
    detected_at: datetime | None,
    source: str,
    operation: str,
    action: str,
) -> None:
    detected_text = detected_at.isoformat() if detected_at is not None else "not-a-time"
    connection.execute(
        """
        INSERT INTO facebook_access_circuit_state (
            profile_scope_key, state, generation, reason_code,
            source_kind, operation_kind, trigger_action_kind,
            last_detected_at, cooldown_until, updated_at
        ) VALUES (?, 'closed', ?, 'facebook_temporary_block', ?, ?, ?, ?, ?, ?)
        """,
        (
            scope,
            generation,
            source,
            operation,
            action,
            detected_text,
            (_NOW + timedelta(hours=12)).isoformat(),
            _NOW.isoformat(),
        ),
    )


def _normalized_sql(value: str) -> str:
    """忽略 SQLite DDL whitespace 差異。"""

    return " ".join(value.split())
