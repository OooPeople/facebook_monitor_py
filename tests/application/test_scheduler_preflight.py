"""Scheduler start preflight tests。"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.scheduler_preflight import run_scheduler_start_preflight
from facebook_monitor.application import scheduler_preflight as scheduler_preflight_module
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.persistence.sqlite_connection import SqliteConnection


def test_scheduler_start_preflight_allows_empty_database(tmp_path: Path) -> None:
    """空 DB 會走正常 schema 初始化，不阻止 scheduler start。"""

    result = run_scheduler_start_preflight(
        tmp_path / "app.db",
        default_interval_seconds=60,
    )

    assert result.ok
    assert result.message == ""


def test_scheduler_start_preflight_defers_transient_sqlite_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """暫時性 SQLite lock 不在 start preflight 永久擋下，交給 resident loop 重試。"""

    class LockedApplicationContext:
        """模擬 preflight 開 DB 時遇到暫時 lock。"""

        def __init__(self, _db_path: Path) -> None:
            pass

        def __enter__(self) -> object:
            raise sqlite3.OperationalError("database is locked")

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

    monkeypatch.setattr(
        scheduler_preflight_module,
        "SqliteApplicationContext",
        LockedApplicationContext,
    )

    result = run_scheduler_start_preflight(
        tmp_path / "app.db",
        default_interval_seconds=60,
    )

    assert result.ok


def test_scheduler_start_preflight_blocks_active_target_decode_violation(
    tmp_path: Path,
) -> None:
    """active target 的核心 enum 壞掉時，preflight 會阻止 scheduler crash-loop。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-1",
                canonical_url="https://www.facebook.com/groups/group-1",
                name="Group 1",
            )
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute(
            "UPDATE targets SET target_kind = ?, paused = 0 WHERE id = ?",
            ("banana", target.id),
        )

    result = run_scheduler_start_preflight(
        db_path,
        default_interval_seconds=60,
    )

    assert not result.ok
    assert "背景掃描啟動前資料檢查失敗" in result.message
    assert f"targets[{target.id}].target_kind" in result.message


def test_scheduler_start_preflight_allows_stale_running_runtime_state(
    tmp_path: Path,
) -> None:
    """stale running 類狀態交給既有 recovery，不在 start preflight 擋下。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="group-1",
                canonical_url="https://www.facebook.com/groups/group-1",
                name="Group 1",
            )
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute(
            "UPDATE targets SET paused = 0 WHERE id = ?",
            (target.id,),
        )
        connection.execute(
            """
            UPDATE target_runtime_state
            SET runtime_status = 'running',
                active_worker_id = '',
                last_started_at = '',
                last_heartbeat_at = ''
            WHERE target_id = ?
            """,
            (target.id,),
        )

    result = run_scheduler_start_preflight(
        db_path,
        default_interval_seconds=60,
    )

    assert result.ok
