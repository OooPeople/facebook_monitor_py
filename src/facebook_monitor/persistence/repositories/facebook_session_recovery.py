"""SQLite repository for stale normal-session durable recovery。

職責：以 generation/request/token CAS 保存獨立 recovery state；不讀 browser、
不修改 circuit，也不寫 scan/latest/seen/outbox。
"""

from __future__ import annotations

from datetime import datetime
import sqlite3

from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryFinishOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryFinishResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryLeaseOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryLeaseResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryReconcileOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryReconcileResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryRequestOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryRequestResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoverySnapshot,
)
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class FacebookSessionRecoveryRepository:
    """保存與轉移 stale normal-session recovery state。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, profile_scope_key: str) -> FacebookSessionRecoverySnapshot | None:
        """讀取單一 profile recovery state。"""

        row = self.connection.execute(
            "SELECT * FROM facebook_session_recovery_state WHERE profile_scope_key = ?",
            (profile_scope_key,),
        ).fetchone()
        return _snapshot(row) if row is not None else None

    def record_stale_session(
        self,
        profile_scope_key: str,
        *,
        marker_session_id: str,
        stale_detected_at: datetime,
        earliest_probe_at: datetime,
    ) -> FacebookSessionRecoveryReconcileResult:
        """建立新 hold；同 marker 重入保持既有 generation/state。"""

        existing = self.get(profile_scope_key)
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO facebook_session_recovery_state (
                    profile_scope_key, generation, status, marker_session_id,
                    stale_detected_at, earliest_probe_at, updated_at
                ) VALUES (?, 1, 'hold', ?, ?, ?, ?)
                """,
                (
                    profile_scope_key,
                    marker_session_id,
                    encode_datetime(stale_detected_at),
                    encode_datetime(earliest_probe_at),
                    encode_datetime(stale_detected_at),
                ),
            )
            return FacebookSessionRecoveryReconcileResult(
                FacebookSessionRecoveryReconcileOutcome.RECORDED,
                self._require(profile_scope_key),
            )
        if existing.marker_session_id == marker_session_id:
            if existing.status == FacebookSessionRecoveryStatus.HOLD:
                self.connection.execute(
                    """
                    UPDATE facebook_session_recovery_state
                    SET earliest_probe_at = CASE
                            WHEN earliest_probe_at > ? THEN earliest_probe_at
                            ELSE ?
                        END,
                        updated_at = ?
                    WHERE profile_scope_key = ?
                      AND generation = ?
                      AND status = 'hold'
                    """,
                    (
                        encode_datetime(earliest_probe_at),
                        encode_datetime(earliest_probe_at),
                        encode_datetime(stale_detected_at),
                        profile_scope_key,
                        existing.generation,
                    ),
                )
            return FacebookSessionRecoveryReconcileResult(
                FacebookSessionRecoveryReconcileOutcome.ALREADY_RECORDED,
                self._require(profile_scope_key),
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_session_recovery_state
            SET generation = generation + 1,
                status = 'hold',
                marker_session_id = ?,
                stale_detected_at = ?,
                earliest_probe_at = ?,
                request_id = '',
                request_requested_at = '',
                requested_target_id = NULL,
                requested_operation_kind = '',
                requested_recipe_kind = '',
                probe_token = '',
                probe_started_at = '',
                probe_lease_expires_at = '',
                last_probe_finished_at = '',
                last_probe_result = '',
                recovered_at = '',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND generation = ?
            """,
            (
                marker_session_id,
                encode_datetime(stale_detected_at),
                encode_datetime(earliest_probe_at),
                encode_datetime(stale_detected_at),
                profile_scope_key,
                existing.generation,
            ),
        )
        if cursor.rowcount != 1:
            return FacebookSessionRecoveryReconcileResult(
                FacebookSessionRecoveryReconcileOutcome.ALREADY_RECORDED,
                self._require(profile_scope_key),
            )
        return FacebookSessionRecoveryReconcileResult(
            FacebookSessionRecoveryReconcileOutcome.RECORDED,
            self._require(profile_scope_key),
        )

    def request_probe(
        self,
        profile_scope_key: str,
        *,
        generation: int,
        request_id: str,
        target_id: str,
        operation_kind: FacebookProductOperationKind,
        recipe_kind: FacebookRecoveryRecipeKind,
        requested_at: datetime,
    ) -> FacebookSessionRecoveryRequestResult:
        """由 hold 以 generation CAS 寫入一次 probe request。"""

        cursor = self.connection.execute(
            """
            UPDATE facebook_session_recovery_state
            SET generation = generation + 1,
                status = 'probe_pending',
                request_id = ?,
                request_requested_at = ?,
                requested_target_id = ?,
                requested_operation_kind = ?,
                requested_recipe_kind = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND generation = ?
              AND status = 'hold'
              AND earliest_probe_at <= ?
            """,
            (
                request_id,
                encode_datetime(requested_at),
                target_id,
                operation_kind.value,
                recipe_kind.value,
                encode_datetime(requested_at),
                profile_scope_key,
                generation,
                encode_datetime(requested_at),
            ),
        )
        state = self._require(profile_scope_key)
        if cursor.rowcount == 1:
            outcome = FacebookSessionRecoveryRequestOutcome.REQUESTED
        elif state.status in {
            FacebookSessionRecoveryStatus.PROBE_PENDING,
            FacebookSessionRecoveryStatus.PROBING,
        }:
            outcome = FacebookSessionRecoveryRequestOutcome.ALREADY_PENDING
        elif state.status == FacebookSessionRecoveryStatus.HOLD and (
            requested_at < state.earliest_probe_at
        ):
            outcome = FacebookSessionRecoveryRequestOutcome.QUIET_PERIOD_ACTIVE
        else:
            outcome = FacebookSessionRecoveryRequestOutcome.REJECTED_STATE
        return FacebookSessionRecoveryRequestResult(outcome, state)

    def claim_probe(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        probe_token: str,
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> FacebookSessionRecoveryClaimResult:
        """以 request id CAS claim probing owner 並推進 generation。"""

        state = self.get(profile_scope_key)
        if state is None:
            return FacebookSessionRecoveryClaimResult(
                FacebookSessionRecoveryClaimOutcome.NOT_FOUND,
                None,
            )
        if state.status != FacebookSessionRecoveryStatus.PROBE_PENDING:
            return FacebookSessionRecoveryClaimResult(
                FacebookSessionRecoveryClaimOutcome.REJECTED_STATE,
                state,
            )
        if state.request_id != request_id:
            return FacebookSessionRecoveryClaimResult(
                FacebookSessionRecoveryClaimOutcome.REQUEST_MISMATCH,
                state,
            )
        if started_at < state.earliest_probe_at:
            return FacebookSessionRecoveryClaimResult(
                FacebookSessionRecoveryClaimOutcome.QUIET_PERIOD_ACTIVE,
                state,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_session_recovery_state
            SET generation = generation + 1,
                status = 'probing',
                probe_token = ?,
                probe_started_at = ?,
                probe_lease_expires_at = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND generation = ?
              AND status = 'probe_pending'
              AND request_id = ?
              AND EXISTS (
                  SELECT 1
                  FROM targets AS probe_target
                  WHERE probe_target.id = requested_target_id
                    AND probe_target.enabled = 1
                    AND probe_target.paused = 0
                    AND (
                        (requested_operation_kind = 'posts_access'
                         AND probe_target.target_kind = 'posts')
                        OR
                        (requested_operation_kind IN (
                            'group_metadata_access', 'cover_metadata_access'
                         )
                         AND probe_target.target_kind IN ('posts', 'comments'))
                    )
              )
            """,
            (
                probe_token,
                encode_datetime(started_at),
                encode_datetime(lease_expires_at),
                encode_datetime(started_at),
                profile_scope_key,
                state.generation,
                request_id,
            ),
        )
        current = self._require(profile_scope_key)
        if cursor.rowcount != 1:
            if (
                current.status == FacebookSessionRecoveryStatus.PROBE_PENDING
                and current.generation == state.generation
                and current.request_id == request_id
            ):
                return FacebookSessionRecoveryClaimResult(
                    FacebookSessionRecoveryClaimOutcome.TARGET_UNAVAILABLE,
                    current,
                    recipe_kind=current.requested_recipe_kind,
                    target_id=current.requested_target_id,
                )
            return FacebookSessionRecoveryClaimResult(
                FacebookSessionRecoveryClaimOutcome.REJECTED_STATE,
                current,
            )
        return FacebookSessionRecoveryClaimResult(
            FacebookSessionRecoveryClaimOutcome.CLAIMED,
            current,
            recipe_kind=current.requested_recipe_kind,
            target_id=current.requested_target_id,
        )

    def finish_probe(
        self,
        profile_scope_key: str,
        *,
        generation: int,
        probe_token: str,
        result: FacebookProbeResult,
        finished_at: datetime,
        next_probe_at: datetime,
    ) -> FacebookSessionRecoveryFinishResult:
        """以 generation + token CAS 完成 probe；只有 success 解除 hold。"""

        success = result == FacebookProbeResult.SUCCESS
        cursor = self.connection.execute(
            """
            UPDATE facebook_session_recovery_state
            SET generation = generation + 1,
                status = ?,
                earliest_probe_at = ?,
                request_id = '',
                request_requested_at = '',
                requested_target_id = NULL,
                requested_operation_kind = '',
                requested_recipe_kind = '',
                probe_token = '',
                probe_started_at = '',
                probe_lease_expires_at = '',
                last_probe_finished_at = ?,
                last_probe_result = ?,
                recovered_at = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND generation = ?
              AND status = 'probing'
              AND probe_token = ?
            """,
            (
                "recovered" if success else "hold",
                encode_datetime(next_probe_at),
                encode_datetime(finished_at),
                result.value,
                encode_datetime(finished_at) if success else "",
                encode_datetime(finished_at),
                profile_scope_key,
                generation,
                probe_token,
            ),
        )
        state = self.get(profile_scope_key)
        return FacebookSessionRecoveryFinishResult(
            (
                FacebookSessionRecoveryFinishOutcome.UPDATED
                if cursor.rowcount == 1
                else (
                    FacebookSessionRecoveryFinishOutcome.STALE_OWNER
                    if state is not None
                    else FacebookSessionRecoveryFinishOutcome.NOT_FOUND
                )
            ),
            state,
        )

    def cancel_pending_probe(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        cancelled_at: datetime,
        next_probe_at: datetime,
    ) -> FacebookSessionRecoveryClaimResult:
        """Target 失效時以 request id 取消 pending request 並回到 hold。"""

        cursor = self.connection.execute(
            """
            UPDATE facebook_session_recovery_state
            SET generation = generation + 1,
                status = 'hold',
                earliest_probe_at = ?,
                request_id = '',
                request_requested_at = '',
                requested_target_id = NULL,
                requested_operation_kind = '',
                requested_recipe_kind = '',
                last_probe_finished_at = ?,
                last_probe_result = 'cancelled',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND status = 'probe_pending'
              AND request_id = ?
            """,
            (
                encode_datetime(next_probe_at),
                encode_datetime(cancelled_at),
                encode_datetime(cancelled_at),
                profile_scope_key,
                request_id,
            ),
        )
        state = self.get(profile_scope_key)
        return FacebookSessionRecoveryClaimResult(
            (
                FacebookSessionRecoveryClaimOutcome.TARGET_UNAVAILABLE
                if cursor.rowcount == 1
                else FacebookSessionRecoveryClaimOutcome.REJECTED_STATE
            ),
            state,
        )

    def recover_expired_probe(
        self,
        profile_scope_key: str,
        *,
        recovered_at: datetime,
        next_probe_at: datetime,
    ) -> FacebookSessionRecoveryLeaseResult:
        """只在 probing lease 到期時 CAS 回 hold 並保留 cancelled result。"""

        state = self.get(profile_scope_key)
        if state is None:
            return FacebookSessionRecoveryLeaseResult(
                FacebookSessionRecoveryLeaseOutcome.NOT_FOUND,
                None,
            )
        if state.status != FacebookSessionRecoveryStatus.PROBING:
            return FacebookSessionRecoveryLeaseResult(
                FacebookSessionRecoveryLeaseOutcome.NOT_PROBING,
                state,
            )
        if (
            state.probe_lease_expires_at is None
            or recovered_at < state.probe_lease_expires_at
        ):
            return FacebookSessionRecoveryLeaseResult(
                FacebookSessionRecoveryLeaseOutcome.NOT_EXPIRED,
                state,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_session_recovery_state
            SET generation = generation + 1,
                status = 'hold',
                earliest_probe_at = ?,
                request_id = '',
                request_requested_at = '',
                requested_target_id = NULL,
                requested_operation_kind = '',
                requested_recipe_kind = '',
                probe_token = '',
                probe_started_at = '',
                probe_lease_expires_at = '',
                last_probe_finished_at = ?,
                last_probe_result = 'cancelled',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND generation = ?
              AND status = 'probing'
              AND probe_lease_expires_at <= ?
            """,
            (
                encode_datetime(next_probe_at),
                encode_datetime(recovered_at),
                encode_datetime(recovered_at),
                profile_scope_key,
                state.generation,
                encode_datetime(recovered_at),
            ),
        )
        current = self._require(profile_scope_key)
        return FacebookSessionRecoveryLeaseResult(
            (
                FacebookSessionRecoveryLeaseOutcome.RECOVERED
                if cursor.rowcount == 1
                else FacebookSessionRecoveryLeaseOutcome.NOT_EXPIRED
            ),
            current,
        )

    def _require(self, profile_scope_key: str) -> FacebookSessionRecoverySnapshot:
        state = self.get(profile_scope_key)
        if state is None:
            raise RuntimeError("Facebook session recovery state disappeared")
        return state


def _snapshot(row: sqlite3.Row) -> FacebookSessionRecoverySnapshot:
    """將 SQLite row 解碼成 typed recovery snapshot。"""

    return FacebookSessionRecoverySnapshot(
        profile_scope_key=str(row["profile_scope_key"]),
        generation=int(row["generation"]),
        status=FacebookSessionRecoveryStatus(str(row["status"])),
        marker_session_id=str(row["marker_session_id"]),
        stale_detected_at=_required_datetime(row["stale_detected_at"]),
        earliest_probe_at=_required_datetime(row["earliest_probe_at"]),
        request_id=str(row["request_id"] or ""),
        request_requested_at=decode_datetime(row["request_requested_at"]),
        requested_target_id=_optional_text(row["requested_target_id"]),
        requested_operation_kind=_operation_kind(row["requested_operation_kind"]),
        requested_recipe_kind=FacebookRecoveryRecipeKind(
            str(row["requested_recipe_kind"] or "")
        ),
        probe_token=str(row["probe_token"] or ""),
        probe_started_at=decode_datetime(row["probe_started_at"]),
        probe_lease_expires_at=decode_datetime(row["probe_lease_expires_at"]),
        last_probe_finished_at=decode_datetime(row["last_probe_finished_at"]),
        last_probe_result=FacebookProbeResult(str(row["last_probe_result"] or "")),
        recovered_at=decode_datetime(row["recovered_at"]),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _operation_kind(value: object) -> FacebookProductOperationKind | None:
    normalized = str(value or "")
    return FacebookProductOperationKind(normalized) if normalized else None


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _required_datetime(value: object) -> datetime:
    decoded = decode_datetime(str(value or ""))
    if decoded is None:
        raise ValueError("required session recovery datetime is missing")
    return decoded


__all__ = ["FacebookSessionRecoveryRepository"]
