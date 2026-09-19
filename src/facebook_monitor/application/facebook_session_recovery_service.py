"""Stale normal-session durable recovery application service。

職責：只協調 DB state、pacing quiet gap 與 target eligibility；不建立 browser，
也不寫 scan、latest item、seen item 或 notification outbox。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from datetime import timedelta
from uuid import UUID
from uuid import uuid4

from facebook_monitor.core.defaults import FacebookAccessDefaults
from facebook_monitor.core.defaults import FacebookAutomationDefaults
from facebook_monitor.core.defaults import PYTHON_FACEBOOK_ACCESS_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_FACEBOOK_AUTOMATION_DEFAULTS
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_automation_pacing import FacebookPacingRecoveryOutcome
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimOutcome,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryClaimResult,
)
from facebook_monitor.core.facebook_session_recovery import (
    FacebookSessionRecoveryFinishResult,
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
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.facebook_automation_pacing import (
    FacebookAutomationPacingRepository,
)
from facebook_monitor.persistence.repositories.facebook_session_recovery import (
    FacebookSessionRecoveryRepository,
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


class FacebookSessionRecoveryService:
    """協調 stale-session hold 的 DB-only request/claim/finish CAS。"""

    def __init__(
        self,
        repository: FacebookSessionRecoveryRepository,
        pacing: FacebookAutomationPacingRepository,
        targets: TargetRepository,
        *,
        automation_defaults: FacebookAutomationDefaults = (
            PYTHON_FACEBOOK_AUTOMATION_DEFAULTS
        ),
        access_defaults: FacebookAccessDefaults = PYTHON_FACEBOOK_ACCESS_DEFAULTS,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.pacing = pacing
        self.targets = targets
        self.automation_defaults = automation_defaults
        self.access_defaults = access_defaults
        self.clock = clock
        self.id_factory = id_factory or (lambda: str(uuid4()))

    def get(self, profile_scope_key: str) -> FacebookSessionRecoverySnapshot | None:
        """讀取 recovery state；不存在時不建立。"""

        return self.repository.get(_required_text(profile_scope_key, "profile scope key"))

    def reconcile_stale_session(
        self,
        profile_scope_key: str,
        *,
        marker_session_id: str,
        reconciled_at: datetime | None = None,
    ) -> FacebookSessionRecoveryReconcileResult:
        """啟動時 DB-only 記錄 hold、清 stale pacing owner並套完整 quiet gap。"""

        now = _require_utc(reconciled_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        owner_id = _required_uuid(marker_session_id, "marker session id")
        next_not_before = now + timedelta(
            seconds=self.automation_defaults.persistent_quiet_gap_seconds
        )
        recovery = self.repository.record_stale_session(
            scope_key,
            marker_session_id=owner_id,
            stale_detected_at=now,
            earliest_probe_at=next_not_before,
        )
        pacing = self.pacing.reconcile_stale_session_owner(
            scope_key,
            marker_session_id=owner_id,
            recovered_at=now,
            next_not_before=next_not_before,
        )
        if pacing.outcome == FacebookPacingRecoveryOutcome.OWNER_MISMATCH:
            return FacebookSessionRecoveryReconcileResult(
                FacebookSessionRecoveryReconcileOutcome.PACING_OWNER_MISMATCH,
                recovery.state,
            )
        return recovery

    def list_probe_target_candidates(
        self,
        profile_scope_key: str,
        *,
        operation_kind: FacebookProductOperationKind,
    ) -> tuple[TargetDescriptor, ...]:
        """列出 hold 可用的同 operation active targets。"""

        state = self.get(profile_scope_key)
        if state is None or state.status != FacebookSessionRecoveryStatus.HOLD:
            return ()
        if operation_kind not in _APPROVED_RECOVERY_RECIPES:
            return ()
        return tuple(
            target
            for target in self.targets.list_enabled()
            if self._target_matches_operation(target, operation_kind=operation_kind)
        )

    def probe_request_readiness(
        self,
        profile_scope_key: str,
        *,
        target_id: str,
        operation_kind: FacebookProductOperationKind,
        checked_at: datetime | None = None,
    ) -> FacebookSessionRecoveryRequestOutcome:
        """唯讀檢查 request eligibility。"""

        now = _require_utc(checked_at or self.clock())
        state = self.get(profile_scope_key)
        if state is None or state.status == FacebookSessionRecoveryStatus.RECOVERED:
            return FacebookSessionRecoveryRequestOutcome.REJECTED_STATE
        if state.status in {
            FacebookSessionRecoveryStatus.PROBE_PENDING,
            FacebookSessionRecoveryStatus.PROBING,
        }:
            return FacebookSessionRecoveryRequestOutcome.ALREADY_PENDING
        if now < state.earliest_probe_at:
            return FacebookSessionRecoveryRequestOutcome.QUIET_PERIOD_ACTIVE
        if operation_kind not in _APPROVED_RECOVERY_RECIPES:
            return FacebookSessionRecoveryRequestOutcome.RECIPE_UNAVAILABLE
        if not self._is_probe_target_eligible(
            target_id,
            operation_kind=operation_kind,
        ):
            return FacebookSessionRecoveryRequestOutcome.TARGET_UNAVAILABLE
        return FacebookSessionRecoveryRequestOutcome.REQUESTED

    def request_probe(
        self,
        profile_scope_key: str,
        *,
        target_id: str,
        operation_kind: FacebookProductOperationKind,
        requested_at: datetime | None = None,
    ) -> FacebookSessionRecoveryRequestResult:
        """驗證 target/recipe 後只持久化 probe request，不執行 browser work。"""

        now = _require_utc(requested_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        normalized_target = _required_text(target_id, "target id")
        state = self.repository.get(scope_key)
        if state is None:
            raise ValueError("stale session recovery state is required")
        readiness = self.probe_request_readiness(
            scope_key,
            target_id=normalized_target,
            operation_kind=operation_kind,
            checked_at=now,
        )
        if readiness != FacebookSessionRecoveryRequestOutcome.REQUESTED:
            return FacebookSessionRecoveryRequestResult(readiness, state)
        recipe = _APPROVED_RECOVERY_RECIPES[operation_kind]
        return self.repository.request_probe(
            scope_key,
            generation=state.generation,
            request_id=_required_uuid(self.id_factory(), "request id"),
            target_id=normalized_target,
            operation_kind=operation_kind,
            recipe_kind=recipe,
            requested_at=now,
        )

    def claim_probe(
        self,
        profile_scope_key: str,
        *,
        request_id: str,
        started_at: datetime | None = None,
    ) -> FacebookSessionRecoveryClaimResult:
        """Browser-free supervisor 以 request id CAS 取得 bounded probe lease。"""

        now = _require_utc(started_at or self.clock())
        scope_key = _required_text(profile_scope_key, "profile scope key")
        normalized_request = _required_uuid(request_id, "request id")
        state = self.repository.get(scope_key)
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
        if state.request_id != normalized_request:
            return FacebookSessionRecoveryClaimResult(
                FacebookSessionRecoveryClaimOutcome.REQUEST_MISMATCH,
                state,
            )
        if (
            not self._is_probe_target_eligible(
                str(state.requested_target_id or ""),
                operation_kind=state.requested_operation_kind,
            )
        ):
            return self.repository.cancel_pending_probe(
                scope_key,
                request_id=normalized_request,
                cancelled_at=now,
                next_probe_at=self._next_quiet_time(now),
            )
        claimed = self.repository.claim_probe(
            scope_key,
            request_id=normalized_request,
            probe_token=_required_uuid(self.id_factory(), "probe token"),
            started_at=now,
            lease_expires_at=now
            + timedelta(seconds=self.access_defaults.half_open_lease_seconds),
        )
        if claimed.outcome != FacebookSessionRecoveryClaimOutcome.TARGET_UNAVAILABLE:
            return claimed
        return self.repository.cancel_pending_probe(
            scope_key,
            request_id=normalized_request,
            cancelled_at=now,
            next_probe_at=self._next_quiet_time(now),
        )

    def finish_probe(
        self,
        profile_scope_key: str,
        *,
        generation: int,
        probe_token: str,
        result: FacebookProbeResult,
        finished_at: datetime | None = None,
    ) -> FacebookSessionRecoveryFinishResult:
        """依 generation + token CAS 完成 probe；success 才解除 recovery hold。"""

        if generation < 1:
            raise ValueError("generation must be positive")
        if result == FacebookProbeResult.NONE:
            raise ValueError("probe result must not be none")
        now = _require_utc(finished_at or self.clock())
        return self.repository.finish_probe(
            _required_text(profile_scope_key, "profile scope key"),
            generation=generation,
            probe_token=_required_uuid(probe_token, "probe token"),
            result=result,
            finished_at=now,
            next_probe_at=self._next_quiet_time(now),
        )

    def recover_expired_probe(
        self,
        profile_scope_key: str,
        *,
        recovered_at: datetime | None = None,
    ) -> FacebookSessionRecoveryLeaseResult:
        """DB-only 回收過期 probing lease並重新套完整 quiet gap。"""

        now = _require_utc(recovered_at or self.clock())
        return self.repository.recover_expired_probe(
            _required_text(profile_scope_key, "profile scope key"),
            recovered_at=now,
            next_probe_at=self._next_quiet_time(now),
        )

    def _next_quiet_time(self, now: datetime) -> datetime:
        return now + timedelta(
            seconds=self.automation_defaults.persistent_quiet_gap_seconds
        )

    def _is_probe_target_eligible(
        self,
        target_id: str,
        *,
        operation_kind: FacebookProductOperationKind | None,
    ) -> bool:
        target = self.targets.get(str(target_id or "").strip())
        return bool(
            target is not None
            and target.enabled
            and not target.paused
            and self._target_matches_operation(
                target,
                operation_kind=operation_kind,
            )
        )

    @staticmethod
    def _target_matches_operation(
        target: TargetDescriptor,
        *,
        operation_kind: FacebookProductOperationKind | None,
    ) -> bool:
        if operation_kind == FacebookProductOperationKind.POSTS_ACCESS:
            return target.target_kind == TargetKind.POSTS
        if operation_kind in {
            FacebookProductOperationKind.GROUP_METADATA_ACCESS,
            FacebookProductOperationKind.COVER_METADATA_ACCESS,
        }:
            return target.target_kind in {TargetKind.POSTS, TargetKind.COMMENTS}
        return False


def _required_text(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _required_uuid(value: str, field_name: str) -> str:
    normalized = _required_text(value, field_name)
    try:
        return str(UUID(normalized))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Facebook session recovery timestamps must be UTC")
    return value


__all__ = ["FacebookSessionRecoveryService"]
