"""SQLite repository for profile-level Facebook access circuit state。

職責：以 narrow conditional updates 保存 circuit、manual probe handoff 與 bounded
transition events；cooldown policy 與 source-specific owner 判斷留在 application layer。
"""

from __future__ import annotations

from datetime import datetime
import sqlite3

from facebook_monitor.core.facebook_access import FacebookAccessCircuitEvent
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookAccessEventKind
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookCircuitTripOutcome
from facebook_monitor.core.facebook_access import FacebookCircuitTripResult
from facebook_monitor.core.facebook_access import FacebookHalfOpenClaimOutcome
from facebook_monitor.core.facebook_access import FacebookHalfOpenClaimResult
from facebook_monitor.core.facebook_access import FacebookLeaseRecoveryOutcome
from facebook_monitor.core.facebook_access import FacebookLeaseRecoveryResult
from facebook_monitor.core.facebook_access import FacebookProbeFinishOutcome
from facebook_monitor.core.facebook_access import FacebookProbeFinishResult
from facebook_monitor.core.facebook_access import FacebookProbeRequestOutcome
from facebook_monitor.core.facebook_access import FacebookProbeRequestResult
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_access import FacebookSafetyHoldOutcome
from facebook_monitor.core.facebook_access import FacebookSafetyHoldResult
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.models import TargetKind
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_datetime


class FacebookAccessCircuitRepository:
    """保存 managed profile 的 Facebook access circuit truth。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, profile_scope_key: str) -> FacebookAccessCircuitSnapshot | None:
        """依 opaque profile scope key 讀取 circuit state。"""

        row = self.connection.execute(
            "SELECT * FROM facebook_access_circuit_state WHERE profile_scope_key = ?",
            (profile_scope_key,),
        ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def ensure_closed(
        self,
        profile_scope_key: str,
        *,
        updated_at: datetime,
    ) -> FacebookAccessCircuitSnapshot:
        """首次看到 managed profile 時建立 generation 0 closed row。"""

        self.connection.execute(
            """
            INSERT OR IGNORE INTO facebook_access_circuit_state (
                profile_scope_key, state, updated_at
            )
            VALUES (?, 'closed', ?)
            """,
            (profile_scope_key, encode_datetime(updated_at)),
        )
        state = self.get(profile_scope_key)
        if state is None:
            raise RuntimeError("failed to initialize Facebook access circuit state")
        return state

    def trip(
        self,
        signal: FacebookAccessBlockSignal,
        *,
        episode_id: str,
        recovery_recipe_kind: FacebookRecoveryRecipeKind,
        opened_at: datetime,
        cooldown_until: datetime,
    ) -> FacebookCircuitTripResult:
        """以 admission generation CAS 將 closed circuit 開啟。"""

        profile_scope_key = signal.admission_token.profile_scope_key
        existing = self.ensure_closed(profile_scope_key, updated_at=opened_at)
        target_id = self._existing_target_id(signal.target_id)
        if existing.status == FacebookAccessCircuitStatus.CLOSED:
            cursor = self.connection.execute(
                """
                UPDATE facebook_access_circuit_state
                SET state = 'open',
                    episode_id = ?,
                    generation = generation + 1,
                    reason_code = ?,
                    source_kind = ?,
                    operation_kind = ?,
                    trigger_action_kind = ?,
                    recovery_recipe_kind = ?,
                    trigger_target_id = ?,
                    opened_at = ?,
                    last_detected_at = ?,
                    cooldown_until = ?,
                    detection_count = 1,
                    reopen_count = 0,
                    half_open_token = '',
                    half_open_started_at = '',
                    half_open_lease_expires_at = '',
                    probe_request_id = '',
                    probe_requested_at = '',
                    requested_recipe_kind = '',
                    requested_target_id = NULL,
                    last_probe_finished_at = '',
                    last_probe_result = '',
                    closed_at = '',
                    updated_at = ?
                WHERE profile_scope_key = ?
                  AND state = 'closed'
                  AND generation = ?
                """,
                (
                    episode_id,
                    signal.reason_code,
                    signal.source_kind.value,
                    signal.operation_kind.value,
                    signal.trigger_action_kind.value,
                    recovery_recipe_kind.value,
                    target_id,
                    encode_datetime(opened_at),
                    encode_datetime(opened_at),
                    encode_datetime(cooldown_until),
                    encode_datetime(opened_at),
                    profile_scope_key,
                    signal.admission_token.db_generation,
                ),
            )
            if cursor.rowcount == 1:
                state = self._require(profile_scope_key)
                self._insert_event(
                    state=state,
                    event_kind=FacebookAccessEventKind.OPENED,
                    from_status=FacebookAccessCircuitStatus.CLOSED,
                    to_status=FacebookAccessCircuitStatus.OPEN,
                    target_id=target_id,
                    occurred_at=opened_at,
                    policy_delay_seconds=_delay_seconds(opened_at, cooldown_until),
                )
                return FacebookCircuitTripResult(
                    outcome=FacebookCircuitTripOutcome.OPENED,
                    state=state,
                )
            existing = self._require(profile_scope_key)

        if (
            existing.status == FacebookAccessCircuitStatus.OPEN
            and existing.generation == signal.admission_token.db_generation + 1
        ):
            cursor = self.connection.execute(
                """
                UPDATE facebook_access_circuit_state
                SET detection_count = detection_count + 1,
                    last_detected_at = ?,
                    updated_at = ?
                WHERE profile_scope_key = ?
                  AND state = 'open'
                  AND generation = ?
                """,
                (
                    encode_datetime(opened_at),
                    encode_datetime(opened_at),
                    profile_scope_key,
                    existing.generation,
                ),
            )
            if cursor.rowcount == 1:
                state = self._require(profile_scope_key)
                self._insert_event(
                    state=state,
                    event_kind=FacebookAccessEventKind.REPEATED_DETECTION,
                    from_status=FacebookAccessCircuitStatus.OPEN,
                    to_status=FacebookAccessCircuitStatus.OPEN,
                    target_id=target_id,
                    occurred_at=opened_at,
                )
                return FacebookCircuitTripResult(
                    outcome=FacebookCircuitTripOutcome.REPEATED,
                    state=state,
                )

        return FacebookCircuitTripResult(
            outcome=FacebookCircuitTripOutcome.REJECTED_STALE_ADMISSION,
            state=self._require(profile_scope_key),
        )

    def open_safety_hold(
        self,
        profile_scope_key: str,
        *,
        episode_id: str,
        reason_code: str,
        operation_kind: FacebookProductOperationKind,
        trigger_action_kind: FacebookActionKind,
        recovery_recipe_kind: FacebookRecoveryRecipeKind,
        opened_at: datetime,
        cooldown_until: datetime,
    ) -> FacebookSafetyHoldResult:
        """將restart不確定性寫成獨立hold，不偽造block signal或target。"""

        existing = self.ensure_closed(profile_scope_key, updated_at=opened_at)
        if existing.status != FacebookAccessCircuitStatus.CLOSED:
            return FacebookSafetyHoldResult(
                outcome=FacebookSafetyHoldOutcome.ALREADY_DURABLE,
                state=existing,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = 'open',
                episode_id = ?,
                generation = generation + 1,
                reason_code = ?,
                source_kind = '',
                operation_kind = ?,
                trigger_action_kind = ?,
                recovery_recipe_kind = ?,
                trigger_target_id = NULL,
                opened_at = ?,
                last_detected_at = '',
                cooldown_until = ?,
                detection_count = 0,
                reopen_count = 0,
                half_open_token = '',
                half_open_started_at = '',
                half_open_lease_expires_at = '',
                probe_request_id = '',
                probe_requested_at = '',
                requested_recipe_kind = '',
                requested_target_id = NULL,
                last_probe_finished_at = '',
                last_probe_result = '',
                closed_at = '',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'closed'
              AND generation = ?
            """,
            (
                episode_id,
                reason_code,
                operation_kind.value,
                trigger_action_kind.value,
                recovery_recipe_kind.value,
                encode_datetime(opened_at),
                encode_datetime(cooldown_until),
                encode_datetime(opened_at),
                profile_scope_key,
                existing.generation,
            ),
        )
        if cursor.rowcount != 1:
            current = self._require(profile_scope_key)
            return FacebookSafetyHoldResult(
                outcome=(
                    FacebookSafetyHoldOutcome.ALREADY_DURABLE
                    if current.status != FacebookAccessCircuitStatus.CLOSED
                    else FacebookSafetyHoldOutcome.REJECTED_STATE
                ),
                state=current,
            )
        state = self._require(profile_scope_key)
        self._insert_event(
            state=state,
            event_kind=FacebookAccessEventKind.OPENED,
            from_status=FacebookAccessCircuitStatus.CLOSED,
            to_status=FacebookAccessCircuitStatus.OPEN,
            target_id=None,
            occurred_at=opened_at,
            policy_delay_seconds=_delay_seconds(opened_at, cooldown_until),
        )
        return FacebookSafetyHoldResult(
            outcome=FacebookSafetyHoldOutcome.OPENED,
            state=state,
        )

    def request_probe(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        recipe_kind: FacebookRecoveryRecipeKind,
        target_id: str | None,
        eligible_target_kinds: tuple[TargetKind, ...],
        requested_at: datetime,
    ) -> FacebookProbeRequestResult:
        """持久化 Web manual recovery request，但不取得 half-open token。"""

        state = self.get(profile_scope_key)
        if state is None or state.status != FacebookAccessCircuitStatus.OPEN:
            return FacebookProbeRequestResult(
                outcome=FacebookProbeRequestOutcome.REJECTED_STATE,
                state=state or _missing_closed_snapshot(profile_scope_key, requested_at),
            )
        if state.probe_request_id:
            return FacebookProbeRequestResult(
                outcome=FacebookProbeRequestOutcome.ALREADY_PENDING,
                state=state,
            )
        if recipe_kind == FacebookRecoveryRecipeKind.NONE:
            return FacebookProbeRequestResult(
                outcome=FacebookProbeRequestOutcome.RECIPE_UNAVAILABLE,
                state=state,
            )
        if state.cooldown_until is not None and requested_at < state.cooldown_until:
            return FacebookProbeRequestResult(
                outcome=FacebookProbeRequestOutcome.COOLDOWN_ACTIVE,
                state=state,
            )
        normalized_target_id = self._existing_target_id(target_id)
        normalized_target_kinds = tuple(kind.value for kind in eligible_target_kinds)
        if normalized_target_id is None or not normalized_target_kinds:
            return FacebookProbeRequestResult(
                outcome=FacebookProbeRequestOutcome.TARGET_UNAVAILABLE,
                state=state,
            )
        kind_placeholders = ", ".join("?" for _ in normalized_target_kinds)
        cursor = self.connection.execute(
            f"""
            UPDATE facebook_access_circuit_state
            SET probe_request_id = ?,
                probe_requested_at = ?,
                requested_recipe_kind = ?,
                requested_target_id = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'open'
              AND generation = ?
              AND probe_request_id = ''
              AND cooldown_until <= ?
              AND EXISTS (
                  SELECT 1
                  FROM targets AS probe_target
                  WHERE probe_target.id = ?
                    AND probe_target.enabled = 1
                    AND probe_target.paused = 0
                    AND probe_target.target_kind IN ({kind_placeholders})
              )
            """,
            (
                request_id,
                encode_datetime(requested_at),
                recipe_kind.value,
                normalized_target_id,
                encode_datetime(requested_at),
                profile_scope_key,
                state.generation,
                encode_datetime(requested_at),
                normalized_target_id,
                *normalized_target_kinds,
            ),
        )
        if cursor.rowcount != 1:
            current = self._require(profile_scope_key)
            outcome = (
                FacebookProbeRequestOutcome.ALREADY_PENDING
                if current.probe_request_id
                else (
                    FacebookProbeRequestOutcome.TARGET_UNAVAILABLE
                    if current.status == FacebookAccessCircuitStatus.OPEN
                    and current.generation == state.generation
                    else FacebookProbeRequestOutcome.REJECTED_STATE
                )
            )
            return FacebookProbeRequestResult(outcome=outcome, state=current)
        current = self._require(profile_scope_key)
        self._insert_event(
            state=current,
            event_kind=FacebookAccessEventKind.PROBE_REQUESTED,
            from_status=FacebookAccessCircuitStatus.OPEN,
            to_status=FacebookAccessCircuitStatus.OPEN,
            target_id=normalized_target_id,
            occurred_at=requested_at,
            recovery_recipe_kind=recipe_kind,
        )
        return FacebookProbeRequestResult(
            outcome=FacebookProbeRequestOutcome.REQUESTED,
            state=current,
        )

    def claim_half_open(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        half_open_token: str,
        eligible_target_kinds: tuple[TargetKind, ...],
        started_at: datetime,
        lease_expires_at: datetime,
    ) -> FacebookHalfOpenClaimResult:
        """由 browser-free supervisor 以 request id CAS 取得 half-open owner。"""

        state = self.get(profile_scope_key)
        if state is None:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.NOT_FOUND,
                state=None,
            )
        if state.status != FacebookAccessCircuitStatus.OPEN:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.REJECTED_STATE,
                state=state,
            )
        if state.probe_request_id != request_id or not request_id:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.REQUEST_MISMATCH,
                state=state,
            )
        if state.cooldown_until is not None and started_at < state.cooldown_until:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.COOLDOWN_ACTIVE,
                state=state,
            )
        recipe_kind = state.requested_recipe_kind
        target_id = state.requested_target_id
        normalized_target_kinds = tuple(kind.value for kind in eligible_target_kinds)
        if target_id is None or not normalized_target_kinds:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.TARGET_UNAVAILABLE,
                state=state,
                recipe_kind=recipe_kind,
                target_id=target_id,
            )
        kind_placeholders = ", ".join("?" for _ in normalized_target_kinds)
        cursor = self.connection.execute(
            f"""
            UPDATE facebook_access_circuit_state
            SET state = 'half_open',
                generation = generation + 1,
                recovery_recipe_kind = requested_recipe_kind,
                half_open_token = ?,
                half_open_started_at = ?,
                half_open_lease_expires_at = ?,
                probe_request_id = '',
                probe_requested_at = '',
                requested_recipe_kind = '',
                requested_target_id = NULL,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'open'
              AND generation = ?
              AND probe_request_id = ?
              AND cooldown_until <= ?
              AND EXISTS (
                  SELECT 1
                  FROM targets AS probe_target
                  WHERE probe_target.id = requested_target_id
                    AND probe_target.enabled = 1
                    AND probe_target.paused = 0
                    AND probe_target.target_kind IN ({kind_placeholders})
              )
            """,
            (
                half_open_token,
                encode_datetime(started_at),
                encode_datetime(lease_expires_at),
                encode_datetime(started_at),
                profile_scope_key,
                state.generation,
                request_id,
                encode_datetime(started_at),
                *normalized_target_kinds,
            ),
        )
        if cursor.rowcount != 1:
            current = self._require(profile_scope_key)
            if (
                current.status == FacebookAccessCircuitStatus.OPEN
                and current.generation == state.generation
                and current.probe_request_id == request_id
            ):
                return FacebookHalfOpenClaimResult(
                    outcome=FacebookHalfOpenClaimOutcome.TARGET_UNAVAILABLE,
                    state=current,
                    recipe_kind=current.requested_recipe_kind,
                    target_id=current.requested_target_id,
                )
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.REQUEST_MISMATCH,
                state=current,
            )
        current = self._require(profile_scope_key)
        self._insert_event(
            state=current,
            event_kind=FacebookAccessEventKind.HALF_OPEN_ACQUIRED,
            from_status=FacebookAccessCircuitStatus.OPEN,
            to_status=FacebookAccessCircuitStatus.HALF_OPEN,
            target_id=target_id,
            occurred_at=started_at,
            recovery_recipe_kind=recipe_kind,
        )
        return FacebookHalfOpenClaimResult(
            outcome=FacebookHalfOpenClaimOutcome.CLAIMED,
            state=current,
            recipe_kind=recipe_kind,
            target_id=target_id,
        )

    def cancel_unavailable_probe_request(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        cancelled_at: datetime,
    ) -> FacebookHalfOpenClaimResult:
        """CAS 取消已無可用 canary 的 pending request。"""

        state = self.get(profile_scope_key)
        if state is None:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.NOT_FOUND,
                state=None,
            )
        if state.status != FacebookAccessCircuitStatus.OPEN:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.REJECTED_STATE,
                state=state,
            )
        if state.probe_request_id != request_id or not request_id:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.REQUEST_MISMATCH,
                state=state,
            )
        target_id = state.requested_target_id
        recipe_kind = state.requested_recipe_kind
        cursor = self.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET probe_request_id = '',
                probe_requested_at = '',
                requested_recipe_kind = '',
                requested_target_id = NULL,
                last_probe_finished_at = ?,
                last_probe_result = 'cancelled',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'open'
              AND generation = ?
              AND probe_request_id = ?
            """,
            (
                encode_datetime(cancelled_at),
                encode_datetime(cancelled_at),
                profile_scope_key,
                state.generation,
                request_id,
            ),
        )
        if cursor.rowcount != 1:
            return FacebookHalfOpenClaimResult(
                outcome=FacebookHalfOpenClaimOutcome.REQUEST_MISMATCH,
                state=self._require(profile_scope_key),
            )
        current = self._require(profile_scope_key)
        self._insert_event(
            state=current,
            event_kind=FacebookAccessEventKind.PROBE_CANCELLED,
            from_status=FacebookAccessCircuitStatus.OPEN,
            to_status=FacebookAccessCircuitStatus.OPEN,
            target_id=target_id,
            occurred_at=cancelled_at,
            recovery_recipe_kind=recipe_kind,
        )
        return FacebookHalfOpenClaimResult(
            outcome=FacebookHalfOpenClaimOutcome.TARGET_UNAVAILABLE,
            state=current,
            recipe_kind=recipe_kind,
            target_id=target_id,
        )

    def close_half_open(
        self,
        profile_scope_key: str,
        *,
        half_open_token: str,
        generation: int,
        finished_at: datetime,
    ) -> FacebookProbeFinishResult:
        """Probe success 時以 token + generation CAS 關閉 circuit。"""

        cursor = self.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = 'closed',
                generation = generation + 1,
                half_open_token = '',
                half_open_started_at = '',
                half_open_lease_expires_at = '',
                last_probe_finished_at = ?,
                last_probe_result = 'success',
                closed_at = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'half_open'
              AND half_open_token = ?
              AND generation = ?
            """,
            (
                encode_datetime(finished_at),
                encode_datetime(finished_at),
                encode_datetime(finished_at),
                profile_scope_key,
                half_open_token,
                generation,
            ),
        )
        if cursor.rowcount != 1:
            return self._stale_finish_result(profile_scope_key)
        state = self._require(profile_scope_key)
        self._insert_event(
            state=state,
            event_kind=FacebookAccessEventKind.PROBE_SUCCEEDED,
            from_status=FacebookAccessCircuitStatus.HALF_OPEN,
            to_status=FacebookAccessCircuitStatus.CLOSED,
            target_id=state.trigger_target_id,
            occurred_at=finished_at,
        )
        return FacebookProbeFinishResult(
            outcome=FacebookProbeFinishOutcome.UPDATED,
            state=state,
        )

    def reopen_half_open(
        self,
        profile_scope_key: str,
        *,
        half_open_token: str,
        generation: int,
        result: FacebookProbeResult,
        finished_at: datetime,
        cooldown_until: datetime,
    ) -> FacebookProbeFinishResult:
        """Probe blocked/inconclusive/cancelled 時回 open 並清 lease。"""

        if result not in {
            FacebookProbeResult.BLOCKED,
            FacebookProbeResult.INCONCLUSIVE,
            FacebookProbeResult.CANCELLED,
        }:
            raise ValueError("reopen result must be blocked, inconclusive or cancelled")
        cursor = self.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = 'open',
                generation = generation + 1,
                reopen_count = reopen_count + ?,
                cooldown_until = ?,
                half_open_token = '',
                half_open_started_at = '',
                half_open_lease_expires_at = '',
                last_probe_finished_at = ?,
                last_probe_result = ?,
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'half_open'
              AND half_open_token = ?
              AND generation = ?
            """,
            (
                int(result == FacebookProbeResult.BLOCKED),
                encode_datetime(cooldown_until),
                encode_datetime(finished_at),
                result.value,
                encode_datetime(finished_at),
                profile_scope_key,
                half_open_token,
                generation,
            ),
        )
        if cursor.rowcount != 1:
            return self._stale_finish_result(profile_scope_key)
        state = self._require(profile_scope_key)
        event_kind = {
            FacebookProbeResult.BLOCKED: FacebookAccessEventKind.PROBE_BLOCKED,
            FacebookProbeResult.INCONCLUSIVE: FacebookAccessEventKind.PROBE_INCONCLUSIVE,
            FacebookProbeResult.CANCELLED: FacebookAccessEventKind.PROBE_CANCELLED,
        }[result]
        self._insert_event(
            state=state,
            event_kind=event_kind,
            from_status=FacebookAccessCircuitStatus.HALF_OPEN,
            to_status=FacebookAccessCircuitStatus.OPEN,
            target_id=state.trigger_target_id,
            occurred_at=finished_at,
            policy_delay_seconds=_delay_seconds(finished_at, cooldown_until),
        )
        return FacebookProbeFinishResult(
            outcome=FacebookProbeFinishOutcome.UPDATED,
            state=state,
        )

    def recover_expired_half_open(
        self,
        profile_scope_key: str,
        *,
        recovered_at: datetime,
        cooldown_until: datetime,
    ) -> FacebookLeaseRecoveryResult:
        """Expired half-open lease 回 open，避免 restart 永久卡住。"""

        state = self.get(profile_scope_key)
        if state is None:
            return FacebookLeaseRecoveryResult(
                outcome=FacebookLeaseRecoveryOutcome.NOT_FOUND,
                state=None,
            )
        if state.status != FacebookAccessCircuitStatus.HALF_OPEN:
            return FacebookLeaseRecoveryResult(
                outcome=FacebookLeaseRecoveryOutcome.NOT_HALF_OPEN,
                state=state,
            )
        if (
            state.half_open_lease_expires_at is None
            or state.half_open_lease_expires_at > recovered_at
        ):
            return FacebookLeaseRecoveryResult(
                outcome=FacebookLeaseRecoveryOutcome.NOT_EXPIRED,
                state=state,
            )
        cursor = self.connection.execute(
            """
            UPDATE facebook_access_circuit_state
            SET state = 'open',
                generation = generation + 1,
                cooldown_until = ?,
                half_open_token = '',
                half_open_started_at = '',
                half_open_lease_expires_at = '',
                last_probe_finished_at = ?,
                last_probe_result = 'cancelled',
                updated_at = ?
            WHERE profile_scope_key = ?
              AND state = 'half_open'
              AND generation = ?
              AND half_open_lease_expires_at <= ?
            """,
            (
                encode_datetime(cooldown_until),
                encode_datetime(recovered_at),
                encode_datetime(recovered_at),
                profile_scope_key,
                state.generation,
                encode_datetime(recovered_at),
            ),
        )
        if cursor.rowcount != 1:
            current = self._require(profile_scope_key)
            return FacebookLeaseRecoveryResult(
                outcome=FacebookLeaseRecoveryOutcome.NOT_EXPIRED,
                state=current,
            )
        current = self._require(profile_scope_key)
        self._insert_event(
            state=current,
            event_kind=FacebookAccessEventKind.LEASE_RECOVERED,
            from_status=FacebookAccessCircuitStatus.HALF_OPEN,
            to_status=FacebookAccessCircuitStatus.OPEN,
            target_id=current.trigger_target_id,
            occurred_at=recovered_at,
            policy_delay_seconds=_delay_seconds(recovered_at, cooldown_until),
        )
        return FacebookLeaseRecoveryResult(
            outcome=FacebookLeaseRecoveryOutcome.RECOVERED,
            state=current,
        )

    def list_recent_events(
        self,
        profile_scope_key: str,
        *,
        limit: int = 50,
    ) -> tuple[FacebookAccessCircuitEvent, ...]:
        """讀取單一 profile 最近 bounded transition events。"""

        rows = self.connection.execute(
            """
            SELECT * FROM facebook_access_circuit_events
            WHERE profile_scope_key = ?
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            (profile_scope_key, max(int(limit), 0)),
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def _insert_event(
        self,
        *,
        state: FacebookAccessCircuitSnapshot,
        event_kind: FacebookAccessEventKind,
        from_status: FacebookAccessCircuitStatus,
        to_status: FacebookAccessCircuitStatus,
        target_id: str | None,
        occurred_at: datetime,
        policy_delay_seconds: int = 0,
        recovery_recipe_kind: FacebookRecoveryRecipeKind | None = None,
    ) -> None:
        """保存 privacy-safe transition event。"""

        self.connection.execute(
            """
            INSERT INTO facebook_access_circuit_events (
                profile_scope_key, episode_id, event_kind, from_state, to_state,
                reason_code, source_kind, operation_kind, trigger_action_kind,
                recovery_recipe_kind, target_id, policy_delay_seconds, occurred_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.profile_scope_key,
                state.episode_id,
                event_kind.value,
                from_status.value,
                to_status.value,
                state.reason_code,
                state.source_kind.value if state.source_kind else "",
                state.operation_kind.value if state.operation_kind else "",
                state.trigger_action_kind.value if state.trigger_action_kind else "",
                (recovery_recipe_kind or state.recovery_recipe_kind).value,
                self._existing_target_id(target_id),
                max(int(policy_delay_seconds), 0),
                encode_datetime(occurred_at),
            ),
        )

    def _existing_target_id(self, target_id: str | None) -> str | None:
        """Target 在 signal 與 transaction 間被刪除時安全降為 NULL。"""

        normalized = str(target_id or "").strip()
        if not normalized:
            return None
        row = self.connection.execute(
            "SELECT 1 FROM targets WHERE id = ?",
            (normalized,),
        ).fetchone()
        return normalized if row is not None else None

    def _require(self, profile_scope_key: str) -> FacebookAccessCircuitSnapshot:
        state = self.get(profile_scope_key)
        if state is None:
            raise RuntimeError("Facebook access circuit state disappeared")
        return state

    def _stale_finish_result(self, profile_scope_key: str) -> FacebookProbeFinishResult:
        state = self.get(profile_scope_key)
        return FacebookProbeFinishResult(
            outcome=(
                FacebookProbeFinishOutcome.STALE_OWNER
                if state is not None
                else FacebookProbeFinishOutcome.NOT_FOUND
            ),
            state=state,
        )


def _snapshot_from_row(row: sqlite3.Row) -> FacebookAccessCircuitSnapshot:
    """將 SQLite row 解碼成 circuit snapshot。"""

    return FacebookAccessCircuitSnapshot(
        profile_scope_key=str(row["profile_scope_key"]),
        status=FacebookAccessCircuitStatus(str(row["state"])),
        episode_id=str(row["episode_id"] or ""),
        generation=int(row["generation"]),
        reason_code=str(row["reason_code"] or ""),
        source_kind=_source_kind(str(row["source_kind"] or "")),
        operation_kind=_operation_kind(str(row["operation_kind"] or "")),
        trigger_action_kind=_action_kind(str(row["trigger_action_kind"] or "")),
        recovery_recipe_kind=FacebookRecoveryRecipeKind(str(row["recovery_recipe_kind"] or "")),
        trigger_target_id=_optional_text(row["trigger_target_id"]),
        opened_at=decode_datetime(str(row["opened_at"] or "")),
        last_detected_at=decode_datetime(str(row["last_detected_at"] or "")),
        cooldown_until=decode_datetime(str(row["cooldown_until"] or "")),
        detection_count=int(row["detection_count"]),
        reopen_count=int(row["reopen_count"]),
        half_open_token=str(row["half_open_token"] or ""),
        half_open_started_at=decode_datetime(str(row["half_open_started_at"] or "")),
        half_open_lease_expires_at=decode_datetime(str(row["half_open_lease_expires_at"] or "")),
        probe_request_id=str(row["probe_request_id"] or ""),
        probe_requested_at=decode_datetime(str(row["probe_requested_at"] or "")),
        requested_recipe_kind=FacebookRecoveryRecipeKind(str(row["requested_recipe_kind"] or "")),
        requested_target_id=_optional_text(row["requested_target_id"]),
        last_probe_finished_at=decode_datetime(str(row["last_probe_finished_at"] or "")),
        last_probe_result=FacebookProbeResult(str(row["last_probe_result"] or "")),
        closed_at=decode_datetime(str(row["closed_at"] or "")),
        updated_at=_required_datetime(row["updated_at"]),
    )


def _event_from_row(row: sqlite3.Row) -> FacebookAccessCircuitEvent:
    """將 SQLite row 解碼成 transition event。"""

    return FacebookAccessCircuitEvent(
        id=int(row["id"]),
        profile_scope_key=str(row["profile_scope_key"]),
        episode_id=str(row["episode_id"]),
        event_kind=FacebookAccessEventKind(str(row["event_kind"])),
        from_status=FacebookAccessCircuitStatus(str(row["from_state"])),
        to_status=FacebookAccessCircuitStatus(str(row["to_state"])),
        reason_code=str(row["reason_code"] or ""),
        source_kind=_source_kind(str(row["source_kind"] or "")),
        operation_kind=_operation_kind(str(row["operation_kind"] or "")),
        trigger_action_kind=_action_kind(str(row["trigger_action_kind"] or "")),
        recovery_recipe_kind=FacebookRecoveryRecipeKind(str(row["recovery_recipe_kind"] or "")),
        target_id=_optional_text(row["target_id"]),
        policy_delay_seconds=int(row["policy_delay_seconds"]),
        occurred_at=_required_datetime(row["occurred_at"]),
    )


def _source_kind(value: str) -> FacebookWorkSourceKind | None:
    return FacebookWorkSourceKind(value) if value else None


def _operation_kind(value: str) -> FacebookProductOperationKind | None:
    return FacebookProductOperationKind(value) if value else None


def _action_kind(value: str) -> FacebookActionKind | None:
    return FacebookActionKind(value) if value else None


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _required_datetime(value: object) -> datetime:
    decoded = decode_datetime(str(value or ""))
    if decoded is None:
        raise ValueError("required circuit datetime is missing")
    return decoded


def _delay_seconds(started_at: datetime, finished_at: datetime) -> int:
    return max(int((finished_at - started_at).total_seconds()), 0)


def _missing_closed_snapshot(
    profile_scope_key: str,
    now: datetime,
) -> FacebookAccessCircuitSnapshot:
    return FacebookAccessCircuitSnapshot(
        profile_scope_key=profile_scope_key,
        updated_at=now,
    )


__all__ = ["FacebookAccessCircuitRepository"]
