"""Facebook access circuit application service。

職責：集中 normal admission、high-confidence trip、人工 probe handoff、cooldown
政策與 half-open owner CAS；不建立 browser，也不執行 recovery recipe。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from facebook_monitor.core.defaults import FacebookAccessDefaults
from facebook_monitor.core.defaults import PYTHON_FACEBOOK_ACCESS_DEFAULTS
from facebook_monitor.core.facebook_access import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.facebook_access import FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON
from facebook_monitor.core.facebook_access import FacebookAccessBlockSignal
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookAccessSignalConfidence
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookAdmissionDecision
from facebook_monitor.core.facebook_access import FacebookAdmissionOutcome
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.core.facebook_access import FacebookCircuitTripOutcome
from facebook_monitor.core.facebook_access import FacebookCircuitTripResult
from facebook_monitor.core.facebook_access import FacebookHalfOpenClaimOutcome
from facebook_monitor.core.facebook_access import FacebookHalfOpenClaimResult
from facebook_monitor.core.facebook_access import FacebookLeaseRecoveryResult
from facebook_monitor.core.facebook_access import FacebookProbeFinishResult
from facebook_monitor.core.facebook_access import FacebookProbeRequestOutcome
from facebook_monitor.core.facebook_access import FacebookProbeRequestResult
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_access import FacebookSafetyHoldResult
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.persistence.repositories.facebook_access_circuit import (
    FacebookAccessCircuitRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


_APPROVED_RECOVERY_RECIPES = {
    FacebookProductOperationKind.POSTS_ACCESS: (
        FacebookRecoveryRecipeKind.GROUP_FEED_DOCUMENT_GUARD_V1
    ),
    FacebookProductOperationKind.GROUP_METADATA_ACCESS: (
        FacebookRecoveryRecipeKind.GROUP_DOCUMENT_GUARD_V1
    ),
    FacebookProductOperationKind.COVER_METADATA_ACCESS: (
        FacebookRecoveryRecipeKind.GROUP_COVER_GUARD_V1
    ),
}


class FacebookAccessCircuitService:
    """協調 profile circuit 狀態與保守 recovery policy。"""

    def __init__(
        self,
        repository: FacebookAccessCircuitRepository,
        targets: TargetRepository,
        *,
        defaults: FacebookAccessDefaults = PYTHON_FACEBOOK_ACCESS_DEFAULTS,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.targets = targets
        self.defaults = defaults
        self.clock = clock
        self.id_factory = id_factory or (lambda: str(uuid4()))

    def get(self, profile_scope_key: str) -> FacebookAccessCircuitSnapshot | None:
        """讀取 profile circuit；不存在時不建立。"""

        return self.repository.get(_required_text(profile_scope_key, "profile scope key"))

    def admit_normal(
        self,
        profile_scope_key: str,
        *,
        process_safety_epoch: int,
        operation_id: str,
        admitted_at: datetime | None = None,
    ) -> FacebookAdmissionDecision:
        """只有 closed 可取得綁定 DB generation 與 process epoch 的 token。"""

        if process_safety_epoch < 0:
            raise ValueError("process safety epoch must be non-negative")
        normalized_scope = _required_text(profile_scope_key, "profile scope key")
        normalized_operation = _required_text(operation_id, "operation id")
        state = self.repository.ensure_closed(
            normalized_scope,
            updated_at=_require_utc_datetime(admitted_at or self.clock()),
        )
        if state.status == FacebookAccessCircuitStatus.CLOSED:
            return FacebookAdmissionDecision(
                outcome=FacebookAdmissionOutcome.ALLOWED,
                state=state,
                token=FacebookAdmissionToken(
                    profile_scope_key=normalized_scope,
                    db_generation=state.generation,
                    process_safety_epoch=process_safety_epoch,
                    operation_id=normalized_operation,
                ),
            )
        outcome = (
            FacebookAdmissionOutcome.DEFERRED_HALF_OPEN
            if state.status == FacebookAccessCircuitStatus.HALF_OPEN
            else FacebookAdmissionOutcome.DEFERRED_OPEN
        )
        return FacebookAdmissionDecision(outcome=outcome, state=state)

    def admission_is_current(
        self,
        token: FacebookAdmissionToken,
        *,
        process_safety_epoch: int,
    ) -> bool:
        """供 visible-write fence 內重驗 process epoch 與 DB generation。"""

        if token.process_safety_epoch != process_safety_epoch:
            return False
        state = self.repository.get(token.profile_scope_key)
        return bool(
            state is not None
            and state.status == FacebookAccessCircuitStatus.CLOSED
            and state.generation == token.db_generation
        )

    def trip(
        self,
        signal: FacebookAccessBlockSignal,
        *,
        source_owner_is_valid: bool,
        detected_at: datetime | None = None,
    ) -> FacebookCircuitTripResult:
        """驗證 typed signal 後，以 admission generation CAS 開啟 circuit。"""

        now = _require_utc_datetime(detected_at or self.clock())
        scope_key = _required_text(
            signal.admission_token.profile_scope_key,
            "profile scope key",
        )
        state = self.repository.ensure_closed(scope_key, updated_at=now)
        if not source_owner_is_valid or not signal.source_owner_token.strip():
            return FacebookCircuitTripResult(
                outcome=FacebookCircuitTripOutcome.REJECTED_OWNER,
                state=state,
            )
        if (
            signal.reason_code != FACEBOOK_TEMPORARY_BLOCK_REASON
            or signal.confidence != FacebookAccessSignalConfidence.HIGH
            or signal.admission_token.db_generation < 0
            or signal.admission_token.process_safety_epoch < 0
            or not signal.admission_token.operation_id.strip()
            or not signal.evidence_code.strip()
        ):
            return FacebookCircuitTripResult(
                outcome=FacebookCircuitTripOutcome.REJECTED_SIGNAL,
                state=state,
            )
        recipe = _APPROVED_RECOVERY_RECIPES.get(
            signal.operation_kind,
            FacebookRecoveryRecipeKind.NONE,
        )
        return self.repository.trip(
            signal,
            episode_id=_required_text(self.id_factory(), "episode id"),
            recovery_recipe_kind=recipe,
            opened_at=now,
            cooldown_until=now + timedelta(seconds=self.defaults.initial_cooldown_seconds),
        )

    def reconcile_persistence_uncertain(
        self,
        profile_scope_key: str,
        *,
        operation_kind: FacebookProductOperationKind,
        trigger_action_kind: FacebookActionKind,
        reconciled_at: datetime | None = None,
    ) -> FacebookSafetyHoldResult:
        """將stale trip marker持久化為不冒充block evidence的安全hold。"""

        now = _require_utc_datetime(reconciled_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        recipe = _APPROVED_RECOVERY_RECIPES.get(
            operation_kind,
            FacebookRecoveryRecipeKind.NONE,
        )
        return self.repository.open_safety_hold(
            scope_key,
            episode_id=_required_text(self.id_factory(), "episode id"),
            reason_code=FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON,
            operation_kind=operation_kind,
            trigger_action_kind=trigger_action_kind,
            recovery_recipe_kind=recipe,
            opened_at=now,
            cooldown_until=now + timedelta(seconds=self.defaults.initial_cooldown_seconds),
        )

    def request_probe(
        self,
        profile_scope_key: str,
        *,
        target_id: str | None,
        requested_at: datetime | None = None,
    ) -> FacebookProbeRequestResult:
        """驗證 operation recipe/canary 後只持久化一次人工 probe request。"""

        now = _require_utc_datetime(requested_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        state = self.repository.get(scope_key)
        if state is None:
            state = self.repository.ensure_closed(scope_key, updated_at=now)
        selected_target = self._select_probe_target(state, target_id=target_id)
        readiness = self._probe_request_readiness(
            state,
            target_id=(selected_target.id if selected_target is not None else target_id),
            checked_at=now,
        )
        if readiness != FacebookProbeRequestOutcome.REQUESTED:
            return FacebookProbeRequestResult(outcome=readiness, state=state)
        approved_recipe = (
            _APPROVED_RECOVERY_RECIPES.get(state.operation_kind)
            if state.operation_kind is not None
            else None
        )
        if approved_recipe is None:
            raise RuntimeError("ready probe request is missing approved recipe")
        if selected_target is None:
            raise RuntimeError("ready probe request is missing eligible target")
        return self.repository.request_probe(
            scope_key,
            request_id=_required_text(self.id_factory(), "probe request id"),
            recipe_kind=approved_recipe,
            target_id=selected_target.id,
            eligible_target_kinds=self._eligible_target_kinds(state.operation_kind),
            requested_at=now,
        )

    def list_probe_target_candidates(
        self,
        profile_scope_key: str,
    ) -> tuple[TargetDescriptor, ...]:
        """列出目前 open episode 同 operation 可用的 active canary。"""

        state = self.repository.get(
            _required_text(profile_scope_key, "profile scope key")
        )
        if state is None or state.status != FacebookAccessCircuitStatus.OPEN:
            return ()
        approved_recipe = (
            _APPROVED_RECOVERY_RECIPES.get(state.operation_kind)
            if state.operation_kind is not None
            else None
        )
        if approved_recipe is None or state.recovery_recipe_kind != approved_recipe:
            return ()
        candidates = [
            target
            for target in self.targets.list_enabled()
            if self._target_matches_operation(
                target,
                operation_kind=state.operation_kind,
            )
        ]
        candidates.sort(
            key=lambda target: (
                target.id != state.trigger_target_id,
                target.created_at,
                target.id,
            )
        )
        return tuple(candidates)

    def probe_request_readiness(
        self,
        profile_scope_key: str,
        *,
        target_id: str | None,
        checked_at: datetime | None = None,
    ) -> FacebookProbeRequestOutcome:
        """唯讀判斷目前是否可持久化一次 manual recovery request。"""

        state = self.repository.get(
            _required_text(profile_scope_key, "profile scope key")
        )
        if state is None:
            return FacebookProbeRequestOutcome.REJECTED_STATE
        selected_target = self._select_probe_target(state, target_id=target_id)
        return self._probe_request_readiness(
            state,
            target_id=(selected_target.id if selected_target is not None else target_id),
            checked_at=_require_utc_datetime(checked_at or self.clock()),
        )

    def _probe_request_readiness(
        self,
        state: FacebookAccessCircuitSnapshot,
        *,
        target_id: str | None,
        checked_at: datetime,
    ) -> FacebookProbeRequestOutcome:
        """集中 request 與 Web read model 共用的 recovery eligibility。"""

        if state.status != FacebookAccessCircuitStatus.OPEN:
            return FacebookProbeRequestOutcome.REJECTED_STATE
        if state.probe_request_id:
            return FacebookProbeRequestOutcome.ALREADY_PENDING
        approved_recipe = (
            _APPROVED_RECOVERY_RECIPES.get(state.operation_kind)
            if state.operation_kind is not None
            else None
        )
        if approved_recipe is None or state.recovery_recipe_kind != approved_recipe:
            return FacebookProbeRequestOutcome.RECIPE_UNAVAILABLE
        if state.cooldown_until is not None and checked_at < state.cooldown_until:
            return FacebookProbeRequestOutcome.COOLDOWN_ACTIVE
        normalized_target = str(target_id or "").strip()
        if not normalized_target or not self._is_probe_target_eligible(
            normalized_target,
            operation_kind=state.operation_kind,
        ):
            return FacebookProbeRequestOutcome.TARGET_UNAVAILABLE
        return FacebookProbeRequestOutcome.REQUESTED

    def claim_half_open(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        started_at: datetime | None = None,
    ) -> FacebookHalfOpenClaimResult:
        """Browser-free supervisor 以 persistent request id CAS 取得 probe owner。"""

        now = _require_utc_datetime(started_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        normalized_request_id = _required_text(request_id, "probe request id")
        state = self.repository.get(scope_key)
        if (
            state is not None
            and state.status == FacebookAccessCircuitStatus.OPEN
            and state.probe_request_id == normalized_request_id
            and not self._is_probe_target_eligible(
                str(state.requested_target_id or ""),
                operation_kind=state.operation_kind,
            )
        ):
            return self.repository.cancel_unavailable_probe_request(
                scope_key,
                request_id=normalized_request_id,
                cancelled_at=now,
            )
        eligible_target_kinds = self._eligible_target_kinds(
            state.operation_kind if state is not None else None
        )
        claimed = self.repository.claim_half_open(
            scope_key,
            request_id=normalized_request_id,
            half_open_token=_required_text(self.id_factory(), "half-open token"),
            eligible_target_kinds=eligible_target_kinds,
            started_at=now,
            lease_expires_at=now + timedelta(seconds=self.defaults.half_open_lease_seconds),
        )
        if claimed.outcome != FacebookHalfOpenClaimOutcome.TARGET_UNAVAILABLE:
            return claimed
        return self.repository.cancel_unavailable_probe_request(
            scope_key,
            request_id=normalized_request_id,
            cancelled_at=now,
        )

    def finish_probe(
        self,
        profile_scope_key: str,
        *,
        half_open_token: str,
        generation: int,
        result: FacebookProbeResult,
        finished_at: datetime | None = None,
    ) -> FacebookProbeFinishResult:
        """依 probe result 以 token + generation CAS 關閉或重新開啟。"""

        now = _require_utc_datetime(finished_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        token = _required_text(half_open_token, "half-open token")
        if generation < 0:
            raise ValueError("generation must be non-negative")
        if result == FacebookProbeResult.SUCCESS:
            return self.repository.close_half_open(
                scope_key,
                half_open_token=token,
                generation=generation,
                finished_at=now,
            )
        if result == FacebookProbeResult.NONE:
            raise ValueError("probe result must not be none")
        delay_seconds = self._reopen_delay_seconds(scope_key, result)
        return self.repository.reopen_half_open(
            scope_key,
            half_open_token=token,
            generation=generation,
            result=result,
            finished_at=now,
            cooldown_until=now + timedelta(seconds=delay_seconds),
        )

    def recover_expired_half_open(
        self,
        profile_scope_key: str,
        *,
        recovered_at: datetime | None = None,
    ) -> FacebookLeaseRecoveryResult:
        """Restart 時只以 DB CAS 回收已到期 half-open lease。"""

        now = _require_utc_datetime(recovered_at or self.clock())
        return self.repository.recover_expired_half_open(
            _required_text(profile_scope_key, "profile scope key"),
            recovered_at=now,
            cooldown_until=now + timedelta(seconds=self.defaults.inconclusive_cooldown_seconds),
        )

    def _reopen_delay_seconds(
        self,
        profile_scope_key: str,
        result: FacebookProbeResult,
    ) -> int:
        if result != FacebookProbeResult.BLOCKED:
            return self.defaults.inconclusive_cooldown_seconds
        state = self.repository.get(profile_scope_key)
        reopen_index = state.reopen_count if state is not None else 0
        delays = self.defaults.reopen_cooldown_seconds
        if not delays:
            raise ValueError("reopen cooldown policy must not be empty")
        return delays[min(reopen_index, len(delays) - 1)]

    def _is_probe_target_eligible(
        self,
        target_id: str,
        *,
        operation_kind: FacebookProductOperationKind | None,
    ) -> bool:
        """只允許 active 且符合產品操作類型的 canary target。"""

        target = self.targets.get(target_id)
        return bool(
            target is not None
            and target.enabled
            and not target.paused
            and self._target_matches_operation(
                target,
                operation_kind=operation_kind,
            )
        )

    def _select_probe_target(
        self,
        state: FacebookAccessCircuitSnapshot,
        *,
        target_id: str | None,
    ) -> TargetDescriptor | None:
        """顯式 target 只驗證該筆；未指定時才選 trigger/替代候選。"""

        normalized_target_id = str(target_id or "").strip()
        if normalized_target_id:
            target = self.targets.get(normalized_target_id)
            if (
                target is not None
                and target.enabled
                and not target.paused
                and self._target_matches_operation(
                    target,
                    operation_kind=state.operation_kind,
                )
            ):
                return target
            return None
        candidates = self.list_probe_target_candidates(state.profile_scope_key)
        return candidates[0] if candidates else None

    @staticmethod
    def _eligible_target_kinds(
        operation_kind: FacebookProductOperationKind | None,
    ) -> tuple[TargetKind, ...]:
        """回傳 repository CAS 可接受的 target kind 集合。"""

        if operation_kind == FacebookProductOperationKind.POSTS_ACCESS:
            return (TargetKind.POSTS,)
        if operation_kind == FacebookProductOperationKind.COMMENTS_ACCESS:
            return (TargetKind.COMMENTS,)
        if operation_kind in {
            FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            FacebookProductOperationKind.COVER_METADATA_ACCESS,
        }:
            return (TargetKind.POSTS, TargetKind.COMMENTS)
        return ()

    @classmethod
    def _target_matches_operation(
        cls,
        target: TargetDescriptor,
        *,
        operation_kind: FacebookProductOperationKind | None,
    ) -> bool:
        """判斷 target kind 是否可執行該產品 operation 的 recipe。"""

        return target.target_kind in cls._eligible_target_kinds(operation_kind)


def _required_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _require_utc_datetime(value: datetime) -> datetime:
    """拒絕 naive 或非 UTC policy timestamp，避免 SQLite 字串比較漂移。"""

    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Facebook access circuit timestamps must be UTC")
    return value


__all__ = ["FacebookAccessCircuitService"]
