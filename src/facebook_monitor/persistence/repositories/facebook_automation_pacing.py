"""SQLite repository for profile-wide Facebook automation pacing。"""

from __future__ import annotations

from datetime import datetime
import sqlite3

from facebook_monitor.core.facebook_automation_pacing import (
    FacebookAutomationPacingSnapshot,
)
from facebook_monitor.core.facebook_automation_pacing import FacebookAutomationPacingToken
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireOutcome
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingAcquireResult
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingFinishOutcome
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingFinishResult
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingRecoveryOutcome
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingRecoveryResult
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class FacebookAutomationPacingRepository:
    """以 generation/operation/owner CAS 保存跨 restart pacing lease。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, profile_scope_key: str) -> FacebookAutomationPacingSnapshot | None:
        """讀取 profile pacing state。"""

        row = self.connection.execute(
            "SELECT * FROM facebook_automation_pacing_state WHERE profile_scope_key = ?",
            (profile_scope_key,),
        ).fetchone()
        return _snapshot(row) if row is not None else None

    def ensure(self, profile_scope_key: str, *, updated_at: datetime) -> FacebookAutomationPacingSnapshot:
        """首次看到 profile 時建立 idle pacing row。"""

        self.connection.execute(
            """
            INSERT OR IGNORE INTO facebook_automation_pacing_state (
                profile_scope_key, updated_at
            ) VALUES (?, ?)
            """,
            (profile_scope_key, encode_datetime(updated_at)),
        )
        state = self.get(profile_scope_key)
        if state is None:
            raise RuntimeError("failed to initialize Facebook automation pacing state")
        return state

    def try_acquire(
        self,
        profile_scope_key: str,
        *,
        operation_id: str,
        work_kind: str,
        owner_session_id: str,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> FacebookPacingAcquireResult:
        """只有無active owner且quiet period已到時才CAS取得lease。"""

        state = self.ensure(profile_scope_key, updated_at=started_at)
        if state.active_operation_id:
            return FacebookPacingAcquireResult(
                outcome=FacebookPacingAcquireOutcome.ACTIVE_LEASE,
                state=state,
            )
        if (
            state.next_automation_not_before is not None
            and started_at < state.next_automation_not_before
        ):
            return FacebookPacingAcquireResult(
                outcome=FacebookPacingAcquireOutcome.QUIET_PERIOD,
                state=state,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_automation_pacing_state
            SET lease_generation = lease_generation + 1,
                active_operation_id = ?,
                active_work_kind = ?,
                owner_session_id = ?,
                active_lease_expires_at = ?,
                last_automation_started_at = ?,
                last_outcome = 'started',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND lease_generation = ?
              AND active_operation_id = ''
              AND (next_automation_not_before = '' OR next_automation_not_before <= ?)
            """,
            (
                operation_id,
                work_kind,
                owner_session_id,
                encode_datetime(lease_expires_at),
                encode_datetime(started_at),
                encode_datetime(started_at),
                profile_scope_key,
                state.lease_generation,
                encode_datetime(started_at),
            ),
        )
        current = self._require(profile_scope_key)
        if cursor.rowcount != 1:
            outcome = (
                FacebookPacingAcquireOutcome.ACTIVE_LEASE
                if current.active_operation_id
                else FacebookPacingAcquireOutcome.QUIET_PERIOD
            )
            return FacebookPacingAcquireResult(outcome=outcome, state=current)
        return FacebookPacingAcquireResult(
            outcome=FacebookPacingAcquireOutcome.ACQUIRED,
            state=current,
            token=FacebookAutomationPacingToken(
                profile_scope_key=profile_scope_key,
                lease_generation=current.lease_generation,
                operation_id=operation_id,
                owner_session_id=owner_session_id,
            ),
        )

    def finish(
        self,
        token: FacebookAutomationPacingToken,
        *,
        finished_at: datetime,
        next_not_before: datetime,
        outcome: str,
    ) -> FacebookPacingFinishResult:
        """完成owner lease並推進persistent quiet period，不觸碰circuit row。"""

        cursor = self.connection.execute(
            """
            UPDATE facebook_automation_pacing_state
            SET active_operation_id = '',
                active_work_kind = '',
                owner_session_id = '',
                active_lease_expires_at = '',
                last_automation_finished_at = ?,
                next_automation_not_before = ?,
                last_outcome = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND lease_generation = ?
              AND active_operation_id = ?
              AND owner_session_id = ?
            """,
            (
                encode_datetime(finished_at),
                encode_datetime(next_not_before),
                outcome,
                encode_datetime(finished_at),
                token.profile_scope_key,
                token.lease_generation,
                token.operation_id,
                token.owner_session_id,
            ),
        )
        state = self._require(token.profile_scope_key)
        return FacebookPacingFinishResult(
            outcome=(
                FacebookPacingFinishOutcome.UPDATED
                if cursor.rowcount == 1
                else FacebookPacingFinishOutcome.STALE_OWNER
            ),
            state=state,
        )

    def recover_expired(
        self,
        profile_scope_key: str,
        *,
        recovered_at: datetime,
        next_not_before: datetime,
    ) -> FacebookPacingRecoveryResult:
        """只在active lease明確過期時CAS清owner並套完整quiet gap。"""

        state = self.get(profile_scope_key)
        if state is None:
            return FacebookPacingRecoveryResult(
                FacebookPacingRecoveryOutcome.NOT_FOUND,
                None,
            )
        if not state.active_operation_id or state.active_lease_expires_at is None:
            return FacebookPacingRecoveryResult(
                FacebookPacingRecoveryOutcome.NOT_ACTIVE,
                state,
            )
        if recovered_at < state.active_lease_expires_at:
            return FacebookPacingRecoveryResult(
                FacebookPacingRecoveryOutcome.NOT_EXPIRED,
                state,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_automation_pacing_state
            SET lease_generation = lease_generation + 1,
                active_operation_id = '',
                active_work_kind = '',
                owner_session_id = '',
                active_lease_expires_at = '',
                last_automation_finished_at = ?,
                next_automation_not_before = ?,
                last_outcome = 'recovered_expired',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND lease_generation = ?
              AND active_operation_id = ?
              AND active_lease_expires_at <= ?
            """,
            (
                encode_datetime(recovered_at),
                encode_datetime(next_not_before),
                encode_datetime(recovered_at),
                profile_scope_key,
                state.lease_generation,
                state.active_operation_id,
                encode_datetime(recovered_at),
            ),
        )
        current = self._require(profile_scope_key)
        return FacebookPacingRecoveryResult(
            (
                FacebookPacingRecoveryOutcome.RECOVERED
                if cursor.rowcount == 1
                else FacebookPacingRecoveryOutcome.NOT_EXPIRED
            ),
            current,
        )

    def reconcile_stale_session_owner(
        self,
        profile_scope_key: str,
        *,
        marker_session_id: str,
        recovered_at: datetime,
        next_not_before: datetime,
    ) -> FacebookPacingRecoveryResult:
        """以 stale marker owner 清除相符 lease，並強制套用完整 quiet gap。"""

        state = self.ensure(profile_scope_key, updated_at=recovered_at)
        if state.active_operation_id and state.owner_session_id != marker_session_id:
            return FacebookPacingRecoveryResult(
                FacebookPacingRecoveryOutcome.OWNER_MISMATCH,
                state,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_automation_pacing_state
            SET lease_generation = lease_generation + 1,
                active_operation_id = '',
                active_work_kind = '',
                owner_session_id = '',
                active_lease_expires_at = '',
                last_automation_finished_at = ?,
                next_automation_not_before = CASE
                    WHEN next_automation_not_before > ? THEN next_automation_not_before
                    ELSE ?
                END,
                last_outcome = 'recovered_stale_session',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND lease_generation = ?
              AND (
                    active_operation_id = ''
                    OR owner_session_id = ?
              )
            """,
            (
                encode_datetime(recovered_at),
                encode_datetime(next_not_before),
                encode_datetime(next_not_before),
                encode_datetime(recovered_at),
                profile_scope_key,
                state.lease_generation,
                marker_session_id,
            ),
        )
        current = self._require(profile_scope_key)
        return FacebookPacingRecoveryResult(
            (
                FacebookPacingRecoveryOutcome.RECOVERED
                if cursor.rowcount == 1
                else FacebookPacingRecoveryOutcome.OWNER_MISMATCH
            ),
            current,
        )

    def _require(self, profile_scope_key: str) -> FacebookAutomationPacingSnapshot:
        state = self.get(profile_scope_key)
        if state is None:
            raise RuntimeError("Facebook automation pacing state disappeared")
        return state


def _snapshot(row: sqlite3.Row) -> FacebookAutomationPacingSnapshot:
    """將sqlite row轉為typed pacing snapshot。"""

    return FacebookAutomationPacingSnapshot(
        profile_scope_key=str(row["profile_scope_key"]),
        lease_generation=int(row["lease_generation"]),
        active_operation_id=str(row["active_operation_id"]),
        active_work_kind=str(row["active_work_kind"]),
        owner_session_id=str(row["owner_session_id"]),
        active_lease_expires_at=decode_datetime(row["active_lease_expires_at"]),
        last_automation_started_at=decode_datetime(row["last_automation_started_at"]),
        last_automation_finished_at=decode_datetime(row["last_automation_finished_at"]),
        next_automation_not_before=decode_datetime(row["next_automation_not_before"]),
        last_outcome=str(row["last_outcome"]),
        updated_at=decode_datetime(row["updated_at"]),
    )


__all__ = ["FacebookAutomationPacingRepository"]
