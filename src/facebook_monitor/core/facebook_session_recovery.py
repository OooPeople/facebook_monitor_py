"""Stale Facebook automation normal-session recovery domain types。

此狀態刻意獨立於 access circuit：非預期中斷不是 Facebook block evidence，
只能經過明確 request、CAS claim 與 bounded probe result 才解除安全 hold。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind


class FacebookSessionRecoveryStatus(StrEnum):
    """Durable stale-session recovery 狀態。"""

    HOLD = "hold"
    PROBE_PENDING = "probe_pending"
    PROBING = "probing"
    RECOVERED = "recovered"


class FacebookSessionRecoveryReconcileOutcome(StrEnum):
    """DB-only stale owner reconciliation 結果。"""

    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"
    PACING_OWNER_MISMATCH = "pacing_owner_mismatch"


class FacebookSessionRecoveryRequestOutcome(StrEnum):
    """Stale-session health probe request 結果。"""

    REQUESTED = "requested"
    ALREADY_PENDING = "already_pending"
    REJECTED_STATE = "rejected_state"
    QUIET_PERIOD_ACTIVE = "quiet_period_active"
    RECIPE_UNAVAILABLE = "recipe_unavailable"
    TARGET_UNAVAILABLE = "target_unavailable"


class FacebookSessionRecoveryClaimOutcome(StrEnum):
    """Persistent probe request CAS claim 結果。"""

    CLAIMED = "claimed"
    NOT_FOUND = "not_found"
    REJECTED_STATE = "rejected_state"
    REQUEST_MISMATCH = "request_mismatch"
    QUIET_PERIOD_ACTIVE = "quiet_period_active"
    TARGET_UNAVAILABLE = "target_unavailable"


class FacebookSessionRecoveryFinishOutcome(StrEnum):
    """Probe owner finish CAS 結果。"""

    UPDATED = "updated"
    NOT_FOUND = "not_found"
    STALE_OWNER = "stale_owner"


class FacebookSessionRecoveryLeaseOutcome(StrEnum):
    """Expired probing lease recovery 結果。"""

    RECOVERED = "recovered"
    NOT_FOUND = "not_found"
    NOT_PROBING = "not_probing"
    NOT_EXPIRED = "not_expired"


@dataclass(frozen=True)
class FacebookSessionRecoverySnapshot:
    """單一 managed profile 的 stale normal-session recovery truth。"""

    profile_scope_key: str
    generation: int
    status: FacebookSessionRecoveryStatus
    marker_session_id: str
    stale_detected_at: datetime
    earliest_probe_at: datetime
    request_id: str = ""
    request_requested_at: datetime | None = None
    requested_target_id: str | None = None
    requested_operation_kind: FacebookProductOperationKind | None = None
    requested_recipe_kind: FacebookRecoveryRecipeKind = (
        FacebookRecoveryRecipeKind.NONE
    )
    probe_token: str = ""
    probe_started_at: datetime | None = None
    probe_lease_expires_at: datetime | None = None
    last_probe_finished_at: datetime | None = None
    last_probe_result: FacebookProbeResult = FacebookProbeResult.NONE
    recovered_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FacebookSessionRecoveryReconcileResult:
    """Stale owner 與 pacing DB-only reconciliation 結果。"""

    outcome: FacebookSessionRecoveryReconcileOutcome
    state: FacebookSessionRecoverySnapshot


@dataclass(frozen=True)
class FacebookSessionRecoveryRequestResult:
    """Probe request 結果。"""

    outcome: FacebookSessionRecoveryRequestOutcome
    state: FacebookSessionRecoverySnapshot


@dataclass(frozen=True)
class FacebookSessionRecoveryClaimResult:
    """Probe claim 結果與 frozen recipe/target。"""

    outcome: FacebookSessionRecoveryClaimOutcome
    state: FacebookSessionRecoverySnapshot | None
    recipe_kind: FacebookRecoveryRecipeKind = FacebookRecoveryRecipeKind.NONE
    target_id: str | None = None


@dataclass(frozen=True)
class FacebookSessionRecoveryFinishResult:
    """Probe finish CAS 結果。"""

    outcome: FacebookSessionRecoveryFinishOutcome
    state: FacebookSessionRecoverySnapshot | None


@dataclass(frozen=True)
class FacebookSessionRecoveryLeaseResult:
    """Expired probe lease recovery 結果。"""

    outcome: FacebookSessionRecoveryLeaseOutcome
    state: FacebookSessionRecoverySnapshot | None


__all__ = [
    "FacebookSessionRecoveryClaimOutcome",
    "FacebookSessionRecoveryClaimResult",
    "FacebookSessionRecoveryFinishOutcome",
    "FacebookSessionRecoveryFinishResult",
    "FacebookSessionRecoveryLeaseOutcome",
    "FacebookSessionRecoveryLeaseResult",
    "FacebookSessionRecoveryReconcileOutcome",
    "FacebookSessionRecoveryReconcileResult",
    "FacebookSessionRecoveryRequestOutcome",
    "FacebookSessionRecoveryRequestResult",
    "FacebookSessionRecoverySnapshot",
    "FacebookSessionRecoveryStatus",
]
