"""Temporary-block incident transaction tests。"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import cast

import pytest

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.target_monitoring_commands import TargetMonitoringCommands
from facebook_monitor.application.target_requests import UpdateTargetStatusRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.facebook_temporary_block import FacebookActionKind
from facebook_monitor.core.facebook_temporary_block import FacebookProductOperationKind
from facebook_monitor.core.facebook_temporary_block import FacebookWorkSourceKind
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.facebook_access_incident import (
    FacebookAccessIncidentOutcomeKind,
)
from facebook_monitor.worker.facebook_access_incident import (
    record_facebook_access_incident_for_db,
)
from facebook_monitor.worker.scan_commit_guard import (
    scan_commit_guard_from_runtime_state,
)


_INCIDENT_AT = datetime(2026, 7, 1, 8, tzinfo=UTC)


def test_each_incident_advances_warning_and_pauses_all_without_normalizing_names(
    tmp_path: Path,
) -> None:
    """首次與再次 block 都全停，且 incident 不順便改 target 名稱或設定。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        first, first_guard = _prepare_running_scan(app, "first", "worker-a", "page-a")
        second = _create_started_target(app, "second")
        pre_paused = _create_started_target(app, "pre-paused")
        app.services.targets.pause_target_monitoring(pre_paused.id)
        connection = app.repositories.targets.connection
        connection.execute(
            """
            UPDATE target_runtime_state
            SET runtime_status = 'error',
                last_error = 'pre-paused-diagnostic',
                consecutive_failure_reason = 'pre_paused_reason',
                consecutive_failure_count = 3
            WHERE target_id = ?
            """,
            (pre_paused.id,),
        )
        pre_paused_runtime_before = tuple(
            connection.execute(
                "SELECT * FROM target_runtime_state WHERE target_id = ?",
                (pre_paused.id,),
            ).fetchone()
        )
        pre_paused_target_before = tuple(
            connection.execute(
                "SELECT * FROM targets WHERE id = ?",
                (pre_paused.id,),
            ).fetchone()
        )
        disabled = _create_started_target(app, "disabled")
        app.services.targets.update_target_status(
            UpdateTargetStatusRequest(
                target_id=disabled.id,
                enabled=False,
                paused=False,
            )
        )
        disabled_target_before = tuple(
            connection.execute(
                "SELECT * FROM targets WHERE id = ?",
                (disabled.id,),
            ).fetchone()
        )
        connection.execute(
            "UPDATE targets SET name = ? WHERE id = ?",
            ("17則通知 · 使用者名稱", first.id),
        )
        config_before = dict(
            connection.execute(
                "SELECT * FROM target_configs WHERE target_id = ?",
                (first.id,),
            ).fetchone()
        )
        _seed_preserved_product_rows(app, first)
        preserved_before = _preserved_product_rows(connection)

    first_outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=_finding(target_id=first.id),
        scan_commit_guard=first_guard,
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        for target_id in (first.id, second.id):
            target = app.repositories.targets.get(target_id)
            assert target is not None and target.paused
        first_after_initial = app.repositories.targets.get(first.id)
        assert first_after_initial is not None
        assert first_after_initial.name == "17則通知 · 使用者名稱"
        app.services.targets.restart_target_monitoring(first.id)
        app.services.targets.restart_target_monitoring(second.id)
        app.repositories.targets.connection.execute(
            "UPDATE targets SET name = ? WHERE id = ?",
            ("17則通知 · 使用者名稱", first.id),
        )
        second_running = app.services.targets.mark_target_running(
            second.id,
            "worker-b",
            page_id="page-b",
        )
        second_guard = scan_commit_guard_from_runtime_state(second_running)

    second_outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=_finding(target_id=second.id),
        scan_commit_guard=second_guard,
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        warning = app.services.facebook_temporary_block_warning.get()
        first_after = app.repositories.targets.get(first.id)
        second_after = app.repositories.targets.get(second.id)
        pre_paused_after = app.repositories.targets.get(pre_paused.id)
        disabled_after = app.repositories.targets.get(disabled.id)
        pre_paused_runtime_after = tuple(
            app.repositories.targets.connection.execute(
                "SELECT * FROM target_runtime_state WHERE target_id = ?",
                (pre_paused.id,),
            ).fetchone()
        )
        pre_paused_target_after = tuple(
            app.repositories.targets.connection.execute(
                "SELECT * FROM targets WHERE id = ?",
                (pre_paused.id,),
            ).fetchone()
        )
        disabled_target_after = tuple(
            app.repositories.targets.connection.execute(
                "SELECT * FROM targets WHERE id = ?",
                (disabled.id,),
            ).fetchone()
        )
        first_runtime = app.repositories.runtime_states.get(first.id)
        config_after = dict(
            app.repositories.targets.connection.execute(
                "SELECT * FROM target_configs WHERE target_id = ?",
                (first.id,),
            ).fetchone()
        )
        preserved_after = _preserved_product_rows(app.repositories.targets.connection)
        scan_count = app.repositories.targets.connection.execute(
            "SELECT COUNT(1) AS count FROM scan_runs"
        ).fetchone()["count"]

    assert first_outcome.kind == FacebookAccessIncidentOutcomeKind.RECORDED
    assert second_outcome.kind == FacebookAccessIncidentOutcomeKind.RECORDED
    assert first_outcome.warning_generation == 1
    assert second_outcome.warning_generation == 2
    assert warning is not None and warning.generation == 2
    assert warning.warning_until == _INCIDENT_AT.replace(hour=20)
    assert first_after is not None and first_after.paused
    assert first_after.name == "17則通知 · 使用者名稱"
    assert second_after is not None and second_after.paused
    assert pre_paused_after is not None and pre_paused_after.paused
    assert pre_paused_runtime_after == pre_paused_runtime_before
    assert pre_paused_target_after == pre_paused_target_before
    assert disabled_after is not None and not disabled_after.enabled
    assert not disabled_after.paused
    assert disabled_target_after == disabled_target_before
    assert first_runtime is not None
    assert first_runtime.desired_state == TargetDesiredState.STOPPED
    assert config_after == config_before
    assert preserved_after == preserved_before
    assert scan_count == 2


def test_non_scan_finding_pauses_all_without_fabricating_scan(tmp_path: Path) -> None:
    """Metadata finding 保存 warning 與全停，但不建立假的 source scan。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = _create_started_target(app, "metadata")

    outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=TemporaryBlockFinding(
            source_kind=FacebookWorkSourceKind.METADATA,
            operation_kind=FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            action_kind=FacebookActionKind.GROUP_DOCUMENT,
            target_id=target.id,
        ),
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        target_after = app.repositories.targets.get(target.id)
        scan_count = app.repositories.targets.connection.execute(
            "SELECT COUNT(1) AS count FROM scan_runs"
        ).fetchone()["count"]

    assert outcome.committed
    assert target_after is not None and target_after.paused
    assert scan_count == 0


def test_non_formal_source_is_rejected_without_warning_or_pause(tmp_path: Path) -> None:
    """即使外部繞過型別檢查，非正式 source 也不得建立 incident。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = _create_started_target(app, "forged-source")

    outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=TemporaryBlockFinding(
            source_kind=cast(FacebookWorkSourceKind, "probe"),
            operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
            action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
            target_id=target.id,
        ),
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        target_after = app.repositories.targets.get(target.id)
        warning = app.services.facebook_temporary_block_warning.get()

    assert outcome.kind == FacebookAccessIncidentOutcomeKind.REJECTED_SIGNAL
    assert target_after is not None and not target_after.paused
    assert warning is None


def test_stale_scan_guard_skips_blocked_scan_but_records_warning_and_pause(
    tmp_path: Path,
) -> None:
    """Scan owner 失配只略過 blocked scan，不得吞掉 confirmed block incident。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target, guard = _prepare_running_scan(app, "stale", "worker-a", "page-a")
        app.repositories.runtime_states.connection.execute(
            "UPDATE target_runtime_state SET active_worker_id = 'worker-new' WHERE target_id = ?",
            (target.id,),
        )

    outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=_finding(target_id=target.id),
        scan_commit_guard=guard,
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        target_after = app.repositories.targets.get(target.id)
        warning = app.services.facebook_temporary_block_warning.get()
        scan_count = app.repositories.targets.connection.execute(
            "SELECT COUNT(1) AS count FROM scan_runs"
        ).fetchone()["count"]

    assert outcome.kind == FacebookAccessIncidentOutcomeKind.RECORDED
    assert outcome.scan_run_id == 0
    assert target_after is not None and target_after.paused
    assert warning is not None and warning.generation == 1
    assert scan_count == 0


def test_incident_pause_all_repairs_corrupt_active_runtime_without_preflight(
    tmp_path: Path,
) -> None:
    """Confirmed block 不得被 startup 專用的 runtime invariant preflight 擋下。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = _create_started_target(app, "corrupt-active-runtime")
        connection = app.repositories.targets.connection
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE target_runtime_state SET runtime_status = 'invalid' WHERE target_id = ?",
            (target.id,),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    outcome = record_facebook_access_incident_for_db(
        db_path=db_path,
        finding=_finding(target_id=target.id),
        occurred_at=_INCIDENT_AT,
    )

    with SqliteApplicationContext(db_path) as app:
        target_after = app.repositories.targets.get(target.id)
        runtime_after = app.repositories.runtime_states.get(target.id)
        warning = app.services.facebook_temporary_block_warning.get()

    assert outcome.kind == FacebookAccessIncidentOutcomeKind.RECORDED
    assert outcome.scan_run_id == 0
    assert target_after is not None and target_after.paused
    assert runtime_after is not None
    assert runtime_after.desired_state == TargetDesiredState.STOPPED
    assert warning is not None and warning.generation == 1


def test_incident_failure_rolls_back_scan_warning_and_partial_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pause 中途失敗時 blocked scan、warning 與 target mutations 必須一起 rollback。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target, guard = _prepare_running_scan(app, "rollback", "worker-a", "page-a")
        peer = _create_started_target(app, "rollback-peer")

    def fail_after_one_pause(self: TargetMonitoringCommands) -> int:
        self.pause_target_monitoring(target.id)
        raise RuntimeError("injected pause failure")

    monkeypatch.setattr(
        TargetMonitoringCommands,
        "pause_all_target_monitoring",
        fail_after_one_pause,
    )
    with pytest.raises(RuntimeError, match="injected pause failure"):
        record_facebook_access_incident_for_db(
            db_path=db_path,
            finding=_finding(target_id=target.id),
            scan_commit_guard=guard,
            occurred_at=_INCIDENT_AT,
        )

    with SqliteApplicationContext(db_path) as app:
        target_after = app.repositories.targets.get(target.id)
        peer_after = app.repositories.targets.get(peer.id)
        warning = app.services.facebook_temporary_block_warning.get()
        scan_count = app.repositories.targets.connection.execute(
            "SELECT COUNT(1) AS count FROM scan_runs"
        ).fetchone()["count"]

    assert target_after is not None and not target_after.paused
    assert peer_after is not None and not peer_after.paused
    assert warning is None
    assert scan_count == 0


def _create_started_target(
    app: ApplicationContext,
    group_id: str,
) -> TargetDescriptor:
    target = app.services.targets.upsert_group_posts_target(
        UpsertGroupPostsTargetRequest(
            group_id=group_id,
            canonical_url=f"https://www.facebook.com/groups/{group_id}",
        )
    )
    return app.services.targets.restart_target_monitoring(target.id)


def _prepare_running_scan(
    app: ApplicationContext,
    group_id: str,
    worker_id: str,
    page_id: str,
) -> tuple[TargetDescriptor, ScanCommitGuard]:
    target = _create_started_target(app, group_id)
    running = app.services.targets.mark_target_running(
        target.id,
        worker_id,
        page_id=page_id,
    )
    return target, scan_commit_guard_from_runtime_state(running)


def _finding(*, target_id: str) -> TemporaryBlockFinding:
    return TemporaryBlockFinding(
        source_kind=FacebookWorkSourceKind.SCAN,
        operation_kind=FacebookProductOperationKind.POSTS_ACCESS,
        action_kind=FacebookActionKind.GROUP_FEED_DOCUMENT,
        target_id=target_id,
    )


def _seed_preserved_product_rows(
    app: ApplicationContext,
    target: TargetDescriptor,
) -> None:
    """建立 incident 不得清除的 seen/history/dedupe/outbox sentinel。"""

    connection = app.repositories.targets.connection
    timestamp = _INCIDENT_AT.isoformat()
    connection.execute(
        """
        INSERT INTO seen_items (
            scope_id, item_key, item_kind, parent_post_id, comment_id,
            first_seen_at, last_seen_at
        ) VALUES (?, 'seen-sentinel', 'post', '', '', ?, ?)
        """,
        (target.scope_id, timestamp, timestamp),
    )
    connection.execute(
        """
        INSERT INTO target_dedupe_state (target_id, dedupe_epoch, updated_at)
        VALUES (?, 7, ?)
        """,
        (target.id, timestamp),
    )
    connection.execute(
        """
        INSERT INTO match_history (
            target_id, group_id, group_name, item_kind, parent_post_id,
            comment_id, item_key, author, text, display_text, permalink,
            include_rule, timestamp_text, recorded_at, created_at
        ) VALUES (?, ?, ?, 'post', '', '', 'history-sentinel', 'author',
                  'text', 'text', 'https://example.test/item', 'rule', '', ?, ?)
        """,
        (target.id, target.group_id, target.group_name, timestamp, timestamp),
    )
    connection.execute(
        """
        INSERT INTO notification_outbox (
            idempotency_key, target_id, item_key, item_kind, channel, status,
            title, message, permalink, attempts, last_error, created_at, updated_at
        ) VALUES ('outbox-sentinel', ?, 'item', 'post', 'desktop', 'pending',
                  'title', 'message', 'https://example.test/item', 0, '', ?, ?)
        """,
        (target.id, timestamp, timestamp),
    )


def _preserved_product_rows(
    connection: sqlite3.Connection,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """讀取 incident 不得改動的產品資料 rows。"""

    table_orders = {
        "seen_items": "scope_id, item_key",
        "target_dedupe_state": "target_id",
        "match_history": "id",
        "notification_outbox": "id",
    }
    return {
        table: tuple(
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY {order_by}"
            ).fetchall()
        )
        for table, order_by in table_orders.items()
    }
