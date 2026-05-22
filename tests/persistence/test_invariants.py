"""SQLite invariant checker tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.persistence.invariants import validate_database_invariants


def test_database_invariants_pass_for_fresh_application_rows(tmp_path: Path) -> None:
    """正常 application service 寫入的資料應通過 invariant checker。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        violations = validate_database_invariants(app.repositories.targets.connection)

    assert violations == ()


def test_database_invariants_report_enum_boolean_range_and_runtime_errors(
    tmp_path: Path,
) -> None:
    """checker 需抓到 enum、boolean、range 與 runtime ownership 異常。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="123",
                canonical_url="https://www.facebook.com/groups/123",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        connection = app.repositories.targets.connection
        connection.execute(
            "UPDATE targets SET target_kind = ?, enabled = ? WHERE id = ?",
            ("pages", 2, target.id),
        )
        connection.execute(
            """
            UPDATE target_configs
            SET min_refresh_sec = 1,
                max_refresh_sec = 0
            WHERE target_id = ?
            """,
            (target.id,),
        )
        connection.execute(
            """
            UPDATE target_runtime_state
            SET active_worker_id = ?,
                active_page_id = ?
            WHERE target_id = ?
            """,
            ("worker-a", "page-a", target.id),
        )
        violations = validate_database_invariants(connection)

    formatted = "\n".join(violation.format() for violation in violations)
    assert "targets" in formatted
    assert "target_kind" in formatted
    assert "enabled" in formatted
    assert "target_configs" in formatted
    assert "refresh_range" in formatted
    assert "target_runtime_state" in formatted
    assert "non-running state must not keep active worker/page ownership" in formatted
