"""SQLite repository implementation。"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.persistence.row_mappers import runtime_state_from_row
from facebook_monitor.persistence.sqlite_codec import encode_datetime


_RUNTIME_STATE_COLUMNS = (
    "target_id",
    "desired_state",
    "runtime_status",
    "scan_requested_at",
    "last_enqueued_at",
    "last_started_at",
    "last_finished_at",
    "last_heartbeat_at",
    "last_error",
    "last_skip_reason",
    "enqueue_reason",
    "active_worker_id",
    "active_page_id",
    "last_page_reloaded_at",
    "scan_guard_count",
    "display_next_due_at",
    "consecutive_failure_reason",
    "consecutive_failure_count",
    "consecutive_scan_skip_reason",
    "consecutive_scan_skip_count",
    "updated_at",
)
_RUNTIME_STATE_UPDATE_COLUMNS = tuple(
    column for column in _RUNTIME_STATE_COLUMNS if column != "target_id"
)
_RUNTIME_STATE_INSERT_COLUMNS_SQL = ", ".join(_RUNTIME_STATE_COLUMNS)
_RUNTIME_STATE_INSERT_VALUES_SQL = ", ".join(
    f":{column}" for column in _RUNTIME_STATE_COLUMNS
)
_RUNTIME_STATE_UPSERT_ASSIGNMENTS_SQL = ",\n                ".join(
    f"{column}=excluded.{column}" for column in _RUNTIME_STATE_UPDATE_COLUMNS
)
_PRESERVED_SCAN_REQUEST_SQL = """CASE
                    WHEN :desired_state = :active_desired_state
                     AND scan_requested_at != ''
                     AND scan_requested_at > :preserve_scan_request_after
                    THEN scan_requested_at
                    ELSE :scan_requested_at
                END"""


def _runtime_state_update_assignments(*, scan_requested_at_sql: str) -> str:
    """建立 full-row UPDATE 欄位清單，避免欄位順序在多處漂移。"""

    return ",\n                ".join(
        f"{column} = {scan_requested_at_sql if column == 'scan_requested_at' else f':{column}'}"
        for column in _RUNTIME_STATE_UPDATE_COLUMNS
    )


_RUNTIME_STATE_UPDATE_ASSIGNMENTS_SQL = _runtime_state_update_assignments(
    scan_requested_at_sql=":scan_requested_at"
)
_RUNTIME_STATE_PRESERVED_SCAN_REQUEST_UPDATE_ASSIGNMENTS_SQL = (
    _runtime_state_update_assignments(scan_requested_at_sql=_PRESERVED_SCAN_REQUEST_SQL)
)


def _runtime_state_bindings(state: TargetRuntimeState) -> dict[str, object]:
    """將 runtime state 編碼成 SQLite named bindings。"""

    return {
        "target_id": state.target_id,
        "desired_state": state.desired_state.value,
        "runtime_status": state.runtime_status.value,
        "scan_requested_at": encode_datetime(state.scan_requested_at),
        "last_enqueued_at": encode_datetime(state.last_enqueued_at),
        "last_started_at": encode_datetime(state.last_started_at),
        "last_finished_at": encode_datetime(state.last_finished_at),
        "last_heartbeat_at": encode_datetime(state.last_heartbeat_at),
        "last_error": state.last_error,
        "last_skip_reason": state.last_skip_reason,
        "enqueue_reason": state.enqueue_reason,
        "active_worker_id": state.active_worker_id,
        "active_page_id": state.active_page_id,
        "last_page_reloaded_at": encode_datetime(state.last_page_reloaded_at),
        "scan_guard_count": state.scan_guard_count,
        "display_next_due_at": encode_datetime(state.display_next_due_at),
        "consecutive_failure_reason": state.consecutive_failure_reason,
        "consecutive_failure_count": state.consecutive_failure_count,
        "consecutive_scan_skip_reason": state.consecutive_scan_skip_reason,
        "consecutive_scan_skip_count": state.consecutive_scan_skip_count,
        "updated_at": encode_datetime(state.updated_at),
    }


class TargetRuntimeStateRepository:
    """保存與查詢 target scheduler runtime state。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, state: TargetRuntimeState) -> None:
        """新增或更新單一 target runtime state。"""

        self.connection.execute(
            f"""
            INSERT INTO target_runtime_state (
                {_RUNTIME_STATE_INSERT_COLUMNS_SQL}
            )
            VALUES ({_RUNTIME_STATE_INSERT_VALUES_SQL})
            ON CONFLICT(target_id) DO UPDATE SET
                {_RUNTIME_STATE_UPSERT_ASSIGNMENTS_SQL}
            """,
            _runtime_state_bindings(state),
        )

    def try_mark_running(
        self,
        target_id: str,
        *,
        worker_id: str,
        page_id: str,
        started_at: datetime,
    ) -> TargetRuntimeState | None:
        """以單一 SQL conditional update 嘗試取得 target running 權。"""

        started_at_text = encode_datetime(started_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                runtime_status = ?,
                last_started_at = ?,
                last_heartbeat_at = ?,
                last_error = '',
                last_skip_reason = '',
                active_worker_id = ?,
                active_page_id = ?,
                updated_at = ?
            WHERE target_id = ?
              AND desired_state = ?
              AND runtime_status IN (?, ?)
            """,
            (
                TargetRuntimeStatus.RUNNING.value,
                started_at_text,
                started_at_text,
                worker_id,
                page_id,
                started_at_text,
                target_id,
                TargetDesiredState.ACTIVE.value,
                TargetRuntimeStatus.IDLE.value,
                TargetRuntimeStatus.QUEUED.value,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def mark_queued_if_not_running(
        self,
        target_id: str,
        *,
        reason: str,
        enqueued_at: datetime,
    ) -> TargetRuntimeState | None:
        """只在 active idle target 標記 queued，避免覆蓋 terminal / owned state。"""

        enqueued_at_text = encode_datetime(enqueued_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                runtime_status = ?,
                last_enqueued_at = ?,
                last_error = '',
                last_skip_reason = '',
                enqueue_reason = ?,
                active_worker_id = '',
                active_page_id = '',
                updated_at = ?
            WHERE target_id = ?
              AND desired_state = ?
              AND runtime_status = ?
            """,
            (
                TargetRuntimeStatus.QUEUED.value,
                enqueued_at_text,
                reason,
                enqueued_at_text,
                target_id,
                TargetDesiredState.ACTIVE.value,
                TargetRuntimeStatus.IDLE.value,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def record_scan_guard_skip(
        self,
        target_id: str,
        *,
        reason: str,
        skipped_at: datetime,
    ) -> TargetRuntimeState | None:
        """Patch 記錄 queue/executor guard skip reason，不覆蓋 ownership 欄位。"""

        skipped_at_text = encode_datetime(skipped_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                last_skip_reason = ?,
                scan_guard_count = scan_guard_count + 1,
                updated_at = ?
            WHERE target_id = ?
            """,
            (reason, skipped_at_text, target_id),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def record_heartbeat_if_running(
        self,
        target_id: str,
        *,
        heartbeat_at: datetime,
        page_id: str = "",
        worker_id: str = "",
    ) -> TargetRuntimeState | None:
        """Patch running heartbeat；指定 worker 時需 owner 相符。"""

        heartbeat_at_text = encode_datetime(heartbeat_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                active_page_id = CASE WHEN ? = '' THEN active_page_id ELSE ? END,
                last_heartbeat_at = ?,
                updated_at = ?
            WHERE target_id = ?
              AND runtime_status = ?
              AND (? = '' OR active_worker_id = ?)
            """,
            (
                page_id,
                page_id,
                heartbeat_at_text,
                heartbeat_at_text,
                target_id,
                TargetRuntimeStatus.RUNNING.value,
                worker_id,
                worker_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def set_display_next_due_at(
        self,
        target_id: str,
        *,
        due_at: datetime | None,
        updated_at: datetime,
    ) -> TargetRuntimeState | None:
        """Patch UI-only display next due 欄位，不覆蓋 runtime ownership。"""

        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                display_next_due_at = ?,
                updated_at = ?
            WHERE target_id = ?
            """,
            (encode_datetime(due_at), encode_datetime(updated_at), target_id),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def set_scan_requested_at(
        self,
        target_id: str,
        *,
        requested_at: datetime | None,
        updated_at: datetime,
    ) -> TargetRuntimeState | None:
        """Patch scan request 欄位，不覆蓋 runtime ownership。"""

        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                scan_requested_at = ?,
                updated_at = ?
            WHERE target_id = ?
            """,
            (encode_datetime(requested_at), encode_datetime(updated_at), target_id),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def clear_scan_request_if_not_newer(
        self,
        target_id: str,
        *,
        consumed_at: datetime,
        updated_at: datetime,
    ) -> TargetRuntimeState | None:
        """只清除未晚於 consumed_at 的 scan request。"""

        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                scan_requested_at = '',
                updated_at = ?
            WHERE target_id = ?
              AND (scan_requested_at = '' OR scan_requested_at <= ?)
            """,
            (encode_datetime(updated_at), target_id, encode_datetime(consumed_at)),
        )
        if cursor.rowcount != 1:
            return self.get(target_id)
        return self.get(target_id)

    def save_if_running_owner(
        self,
        state: TargetRuntimeState,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """只在目前 running owner 仍相同時更新 runtime state。"""

        started_at_text = encode_datetime(started_at)
        bindings = _runtime_state_bindings(state)
        bindings.update(
            {
                "active_desired_state": TargetDesiredState.ACTIVE.value,
                "preserve_scan_request_after": started_at_text,
                "expected_runtime_status": TargetRuntimeStatus.RUNNING.value,
                "expected_worker_id": worker_id,
                "expected_started_at": started_at_text,
                "expected_page_id": page_id,
            }
        )
        cursor = self.connection.execute(
            f"""
            UPDATE target_runtime_state
            SET
                {_RUNTIME_STATE_PRESERVED_SCAN_REQUEST_UPDATE_ASSIGNMENTS_SQL}
            WHERE target_id = :target_id
              AND runtime_status = :expected_runtime_status
              AND active_worker_id = :expected_worker_id
              AND last_started_at = :expected_started_at
              AND (:expected_page_id = '' OR active_page_id = :expected_page_id)
            """,
            bindings,
        )
        if cursor.rowcount != 1:
            return None
        return self.get(state.target_id)

    def save_if_not_running(self, state: TargetRuntimeState) -> TargetRuntimeState | None:
        """只在目前 row 不是 running owner 時保存 state。"""

        bindings = _runtime_state_bindings(state)
        bindings["running_status"] = TargetRuntimeStatus.RUNNING.value
        cursor = self.connection.execute(
            f"""
            UPDATE target_runtime_state
            SET
                {_RUNTIME_STATE_UPDATE_ASSIGNMENTS_SQL}
            WHERE target_id = :target_id
              AND runtime_status != :running_status
            """,
            bindings,
        )
        if cursor.rowcount != 1:
            return None
        return self.get(state.target_id)

    def record_heartbeat_if_running_owner(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        heartbeat_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """只在目前 running owner 仍相同時刷新 heartbeat。"""

        started_at_text = encode_datetime(started_at)
        heartbeat_at_text = encode_datetime(heartbeat_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                active_page_id = CASE WHEN ? = '' THEN active_page_id ELSE ? END,
                last_heartbeat_at = ?,
                updated_at = ?
            WHERE target_id = ?
              AND runtime_status = ?
              AND active_worker_id = ?
              AND last_started_at = ?
              AND (? = '' OR active_page_id = ?)
            """,
            (
                page_id,
                page_id,
                heartbeat_at_text,
                heartbeat_at_text,
                target_id,
                TargetRuntimeStatus.RUNNING.value,
                worker_id,
                started_at_text,
                page_id,
                page_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def mark_page_reloaded_if_running_owner(
        self,
        target_id: str,
        *,
        worker_id: str,
        started_at: datetime,
        reloaded_at: datetime,
        heartbeat_at: datetime,
        page_id: str = "",
    ) -> TargetRuntimeState | None:
        """只在目前 running owner 仍相同時記錄 page reload/goto。"""

        started_at_text = encode_datetime(started_at)
        heartbeat_at_text = encode_datetime(heartbeat_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                active_page_id = CASE WHEN ? = '' THEN active_page_id ELSE ? END,
                last_page_reloaded_at = ?,
                last_heartbeat_at = ?,
                updated_at = ?
            WHERE target_id = ?
              AND runtime_status = ?
              AND active_worker_id = ?
              AND last_started_at = ?
              AND (? = '' OR active_page_id = ?)
            """,
            (
                page_id,
                page_id,
                encode_datetime(reloaded_at),
                heartbeat_at_text,
                heartbeat_at_text,
                target_id,
                TargetRuntimeStatus.RUNNING.value,
                worker_id,
                started_at_text,
                page_id,
                page_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(target_id)

    def save_stale_running_state_if_unchanged(
        self,
        state: TargetRuntimeState,
        *,
        worker_id: str,
        started_at: datetime,
        page_id: str = "",
        stale_before: datetime,
    ) -> TargetRuntimeState | None:
        """只在 row 仍是同一個 stale running owner 時保存 recovery state。"""

        bindings = _runtime_state_bindings(state)
        bindings.update(
            {
                "active_desired_state": TargetDesiredState.ACTIVE.value,
                "preserve_scan_request_after": encode_datetime(started_at),
                "expected_runtime_status": TargetRuntimeStatus.RUNNING.value,
                "expected_desired_state": state.desired_state.value,
                "expected_worker_id": worker_id,
                "expected_started_at": encode_datetime(started_at),
                "expected_page_id": page_id,
                "stale_before": encode_datetime(stale_before),
            }
        )
        cursor = self.connection.execute(
            f"""
            UPDATE target_runtime_state
            SET
                {_RUNTIME_STATE_PRESERVED_SCAN_REQUEST_UPDATE_ASSIGNMENTS_SQL}
            WHERE target_id = :target_id
              AND runtime_status = :expected_runtime_status
              AND desired_state = :expected_desired_state
              AND active_worker_id = :expected_worker_id
              AND last_started_at = :expected_started_at
              AND (:expected_page_id = '' OR active_page_id = :expected_page_id)
              AND (
                (last_heartbeat_at != '' AND last_heartbeat_at <= :stale_before)
                OR (last_heartbeat_at = '' AND updated_at <= :stale_before)
              )
            """,
            bindings,
        )
        if cursor.rowcount != 1:
            return None
        return self.get(state.target_id)

    def save_stale_queued_state_if_unchanged(
        self,
        state: TargetRuntimeState,
        *,
        expected_enqueued_at: datetime | None,
        expected_updated_at: datetime,
        stale_before: datetime,
    ) -> TargetRuntimeState | None:
        """只在 row 仍是 stale queued 時保存 recovery state。"""

        expected_enqueued_at_text = encode_datetime(expected_enqueued_at)
        cursor = self.connection.execute(
            """
            UPDATE target_runtime_state
            SET
                runtime_status = ?,
                last_error = ?,
                last_skip_reason = ?,
                enqueue_reason = ?,
                active_worker_id = ?,
                active_page_id = ?,
                updated_at = ?
            WHERE target_id = ?
              AND desired_state = ?
              AND runtime_status = ?
              AND ((last_enqueued_at IS NULL AND ? IS NULL) OR last_enqueued_at = ?)
              AND updated_at = ?
              AND (
                (last_enqueued_at != '' AND last_enqueued_at <= ?)
                OR (last_enqueued_at = '' AND updated_at <= ?)
              )
            """,
            (
                state.runtime_status.value,
                state.last_error,
                state.last_skip_reason,
                state.enqueue_reason,
                state.active_worker_id,
                state.active_page_id,
                encode_datetime(state.updated_at),
                state.target_id,
                TargetDesiredState.ACTIVE.value,
                TargetRuntimeStatus.QUEUED.value,
                expected_enqueued_at_text,
                expected_enqueued_at_text,
                encode_datetime(expected_updated_at),
                encode_datetime(stale_before),
                encode_datetime(stale_before),
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.get(state.target_id)

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

    def list_stale_running_candidates(self, *, stale_before: datetime) -> list[TargetRuntimeState]:
        """列出 stale running recovery 候選，避免全表 decode inactive corrupt rows。"""

        stale_before_text = encode_datetime(stale_before)
        rows = self.connection.execute(
            """
            SELECT * FROM target_runtime_state
            WHERE runtime_status = ?
              AND (
                (last_heartbeat_at != '' AND last_heartbeat_at <= ?)
                OR (last_heartbeat_at = '' AND updated_at <= ?)
              )
            ORDER BY updated_at
            """,
            (
                TargetRuntimeStatus.RUNNING.value,
                stale_before_text,
                stale_before_text,
            ),
        ).fetchall()
        return [runtime_state_from_row(row) for row in rows]

    def list_stale_queued_candidates(self, *, stale_before: datetime) -> list[TargetRuntimeState]:
        """列出 stale queued recovery 候選，避免全表 decode inactive corrupt rows。"""

        stale_before_text = encode_datetime(stale_before)
        rows = self.connection.execute(
            """
            SELECT * FROM target_runtime_state
            WHERE runtime_status = ?
              AND (
                (last_enqueued_at != '' AND last_enqueued_at <= ?)
                OR (last_enqueued_at = '' AND updated_at <= ?)
              )
            ORDER BY updated_at
            """,
            (
                TargetRuntimeStatus.QUEUED.value,
                stale_before_text,
                stale_before_text,
            ),
        ).fetchall()
        return [runtime_state_from_row(row) for row in rows]
