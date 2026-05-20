"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.persistence.row_mappers import runtime_state_from_row
from facebook_monitor.persistence.sqlite_codec import encode_datetime

class TargetRuntimeStateRepository:
    """保存與查詢 target scheduler runtime state。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, state: TargetRuntimeState) -> None:
        """新增或更新單一 target runtime state。"""

        self.connection.execute(
            """
            INSERT INTO target_runtime_state (
                target_id, desired_state, runtime_status, scan_requested_at, last_enqueued_at,
                last_started_at, last_finished_at, last_heartbeat_at, last_error,
                last_skip_reason, enqueue_reason, active_worker_id, active_page_id,
                last_page_reloaded_at, scan_guard_count, display_next_due_at,
                consecutive_failure_reason, consecutive_failure_count, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                desired_state=excluded.desired_state,
                runtime_status=excluded.runtime_status,
                scan_requested_at=excluded.scan_requested_at,
                last_enqueued_at=excluded.last_enqueued_at,
                last_started_at=excluded.last_started_at,
                last_finished_at=excluded.last_finished_at,
                last_heartbeat_at=excluded.last_heartbeat_at,
                last_error=excluded.last_error,
                last_skip_reason=excluded.last_skip_reason,
                enqueue_reason=excluded.enqueue_reason,
                active_worker_id=excluded.active_worker_id,
                active_page_id=excluded.active_page_id,
                last_page_reloaded_at=excluded.last_page_reloaded_at,
                scan_guard_count=excluded.scan_guard_count,
                display_next_due_at=excluded.display_next_due_at,
                consecutive_failure_reason=excluded.consecutive_failure_reason,
                consecutive_failure_count=excluded.consecutive_failure_count,
                updated_at=excluded.updated_at
            """,
            (
                state.target_id,
                state.desired_state.value,
                state.runtime_status.value,
                encode_datetime(state.scan_requested_at),
                encode_datetime(state.last_enqueued_at),
                encode_datetime(state.last_started_at),
                encode_datetime(state.last_finished_at),
                encode_datetime(state.last_heartbeat_at),
                state.last_error,
                state.last_skip_reason,
                state.enqueue_reason,
                state.active_worker_id,
                state.active_page_id,
                encode_datetime(state.last_page_reloaded_at),
                state.scan_guard_count,
                encode_datetime(state.display_next_due_at),
                state.consecutive_failure_reason,
                state.consecutive_failure_count,
                encode_datetime(state.updated_at),
            ),
        )

    def get(self, target_id: str) -> TargetRuntimeState | None:
        """依 target id 查詢 runtime state。"""

        row = self.connection.execute(
            "SELECT * FROM target_runtime_state WHERE target_id = ?",
            (target_id,),
        ).fetchone()
        return runtime_state_from_row(row) if row else None

    def list_by_targets(self, target_ids: list[str]) -> dict[str, TargetRuntimeState]:
        """一次查詢多個 target runtime state。"""

        unique_target_ids = list(dict.fromkeys(target_id for target_id in target_ids if target_id))
        if not unique_target_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_target_ids)
        rows = self.connection.execute(
            f"""
            SELECT * FROM target_runtime_state
            WHERE target_id IN ({placeholders})
            """,
            tuple(unique_target_ids),
        ).fetchall()
        states: dict[str, TargetRuntimeState] = {}
        for row in rows:
            state = runtime_state_from_row(row)
            states[state.target_id] = state
        return states

    def list_desired_active(self) -> list[TargetRuntimeState]:
        """列出期望由 scheduler 掃描的 target runtime state。"""

        rows = self.connection.execute(
            """
            SELECT * FROM target_runtime_state
            WHERE desired_state = ?
            ORDER BY updated_at
            """,
            (TargetDesiredState.ACTIVE.value,),
        ).fetchall()
        return [runtime_state_from_row(row) for row in rows]

    def list_all(self) -> list[TargetRuntimeState]:
        """列出所有 target runtime state，供 stale recovery 使用。"""

        rows = self.connection.execute(
            "SELECT * FROM target_runtime_state ORDER BY updated_at"
        ).fetchall()
        return [runtime_state_from_row(row) for row in rows]

