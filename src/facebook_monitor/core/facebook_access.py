"""Facebook automation profile 存取安全 domain types。

職責：定義 profile 級 circuit、admission token、typed block signal 與 CAS 結果，
不依賴 Playwright、SQLite 或 Web UI。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON


FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON = "facebook_access_persistence_uncertain"


class FacebookAccessCircuitStatus(StrEnum):
    """Profile 級 Facebook access circuit 狀態。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FacebookAccessSignalConfidence(StrEnum):
    """Page-access signal 信心等級。"""

    HIGH = "high"


class FacebookWorkSourceKind(StrEnum):
    """觸發 Facebook work 的正式來源。"""

    SCAN = "scan"
    METADATA = "metadata"
    COVER = "cover"
    SYNC_RESOLVER = "sync_resolver"
    PROBE = "probe"


class FacebookProductOperationKind(StrEnum):
    """不綁 transport 的 Facebook 產品操作意圖。"""

    POSTS_ACCESS = "posts_access"
    COMMENTS_ACCESS = "comments_access"
    GROUP_METADATA_ACCESS = "group_metadata_access"
    COVER_METADATA_ACCESS = "cover_metadata_access"
    UNKNOWN = "unknown"


class FacebookActionKind(StrEnum):
    """實際觸發 signal 的 browser action。"""

    GROUP_FEED_DOCUMENT = "group_feed_document"
    GROUP_DOCUMENT = "group_document"
    DIRECT_DOCUMENT = "direct_document"
    RELOAD = "reload"
    TRUSTED_CLICK = "trusted_click"
    UNKNOWN = "unknown"


class FacebookRecoveryRecipeKind(StrEnum):
    """Half-open 可執行的版本化安全 recipe。"""

    NONE = ""
    GROUP_FEED_DOCUMENT_GUARD_V1 = "group_feed_document_guard_v1"
    COMMENTS_GROUP_TRUSTED_CLICK_V1 = "comments_group_trusted_click_v1"
    GROUP_DOCUMENT_GUARD_V1 = "group_document_guard_v1"
    GROUP_COVER_GUARD_V1 = "group_cover_guard_v1"


class FacebookProbeResult(StrEnum):
    """最近一次 half-open probe 結果。"""

    NONE = ""
    SUCCESS = "success"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


class FacebookProbeFailureStage(StrEnum):
    """Recovery probe 可安全寫入 log / diagnostics 的固定失敗階段。"""

    RESOURCE_ACQUIRE = "resource_acquire"
    BROWSER_LAUNCH = "browser_launch"
    PAGE_CREATE = "page_create"
    NAVIGATION = "navigation"
    PAGE_GUARD = "page_guard"
    ROUTE_IDENTITY = "route_identity"
    CONTEXT_CLOSE = "context_close"
    DEADLINE = "deadline"


class FacebookAccessEventKind(StrEnum):
    """Circuit transition audit event 類型。"""

    OPENED = "opened"
    REPEATED_DETECTION = "repeated_detection"
    PROBE_REQUESTED = "probe_requested"
    HALF_OPEN_ACQUIRED = "half_open_acquired"
    PROBE_SUCCEEDED = "probe_succeeded"
    PROBE_BLOCKED = "probe_blocked"
    PROBE_INCONCLUSIVE = "probe_inconclusive"
    PROBE_CANCELLED = "probe_cancelled"
    LEASE_RECOVERED = "lease_recovered"
    CLOSED = "closed"


class FacebookAdmissionOutcome(StrEnum):
    """Normal Facebook work admission 結果。"""

    ALLOWED = "allowed"
    DEFERRED_OPEN = "deferred_open"
    DEFERRED_HALF_OPEN = "deferred_half_open"


class FacebookCircuitTripOutcome(StrEnum):
    """High-confidence signal 寫入 circuit 的結果。"""

    OPENED = "opened"
    REPEATED = "repeated"
    REJECTED_OWNER = "rejected_owner"
    REJECTED_SIGNAL = "rejected_signal"
    REJECTED_STALE_ADMISSION = "rejected_stale_admission"


class FacebookSafetyHoldOutcome(StrEnum):
    """Restart reconciliation 寫入 durable safety hold 的結果。"""

    OPENED = "opened"
    ALREADY_DURABLE = "already_durable"
    REJECTED_STATE = "rejected_state"


class FacebookProbeRequestOutcome(StrEnum):
    """人工 recovery request 持久化結果。"""

    REQUESTED = "requested"
    ALREADY_PENDING = "already_pending"
    REJECTED_STATE = "rejected_state"
    COOLDOWN_ACTIVE = "cooldown_active"
    RECIPE_UNAVAILABLE = "recipe_unavailable"
    TARGET_UNAVAILABLE = "target_unavailable"


class FacebookHalfOpenClaimOutcome(StrEnum):
    """Browser-free supervisor claim probe request 的結果。"""

    CLAIMED = "claimed"
    NOT_FOUND = "not_found"
    REJECTED_STATE = "rejected_state"
    REQUEST_MISMATCH = "request_mismatch"
    COOLDOWN_ACTIVE = "cooldown_active"
    TARGET_UNAVAILABLE = "target_unavailable"


class FacebookProbeFinishOutcome(StrEnum):
    """Half-open owner 完成 transition 的 CAS 結果。"""

    UPDATED = "updated"
    NOT_FOUND = "not_found"
    STALE_OWNER = "stale_owner"


class FacebookLeaseRecoveryOutcome(StrEnum):
    """Crash 後 half-open lease recovery 結果。"""

    RECOVERED = "recovered"
    NOT_FOUND = "not_found"
    NOT_HALF_OPEN = "not_half_open"
    NOT_EXPIRED = "not_expired"


@dataclass(frozen=True)
class FacebookAdmissionToken:
    """綁定 DB generation 與 process write-fence epoch 的 admission token。"""

    profile_scope_key: str
    db_generation: int
    process_safety_epoch: int
    operation_id: str


@dataclass(frozen=True)
class FacebookAccessBlockSignal:
    """High-confidence page guard 交給 circuit service 的 typed signal。"""

    admission_token: FacebookAdmissionToken
    source_kind: FacebookWorkSourceKind
    operation_kind: FacebookProductOperationKind
    trigger_action_kind: FacebookActionKind
    source_owner_token: str
    target_id: str | None = None
    evidence_code: str = "facebook_page_guard"
    reason_code: Literal["facebook_temporary_block"] = "facebook_temporary_block"
    confidence: FacebookAccessSignalConfidence = FacebookAccessSignalConfidence.HIGH


@dataclass(frozen=True)
class FacebookAccessCircuitSnapshot:
    """保存單一 managed profile 的 persistent circuit state。"""

    profile_scope_key: str
    status: FacebookAccessCircuitStatus = FacebookAccessCircuitStatus.CLOSED
    episode_id: str = ""
    generation: int = 0
    reason_code: str = ""
    source_kind: FacebookWorkSourceKind | None = None
    operation_kind: FacebookProductOperationKind | None = None
    trigger_action_kind: FacebookActionKind | None = None
    recovery_recipe_kind: FacebookRecoveryRecipeKind = FacebookRecoveryRecipeKind.NONE
    trigger_target_id: str | None = None
    opened_at: datetime | None = None
    last_detected_at: datetime | None = None
    cooldown_until: datetime | None = None
    detection_count: int = 0
    reopen_count: int = 0
    half_open_token: str = ""
    half_open_started_at: datetime | None = None
    half_open_lease_expires_at: datetime | None = None
    probe_request_id: str = ""
    probe_requested_at: datetime | None = None
    requested_recipe_kind: FacebookRecoveryRecipeKind = FacebookRecoveryRecipeKind.NONE
    requested_target_id: str | None = None
    last_probe_finished_at: datetime | None = None
    last_probe_result: FacebookProbeResult = FacebookProbeResult.NONE
    closed_at: datetime | None = None
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class FacebookAccessCircuitEvent:
    """保存一筆不含 URL/page body 的 circuit transition event。"""

    id: int
    profile_scope_key: str
    episode_id: str
    event_kind: FacebookAccessEventKind
    from_status: FacebookAccessCircuitStatus
    to_status: FacebookAccessCircuitStatus
    reason_code: str = ""
    source_kind: FacebookWorkSourceKind | None = None
    operation_kind: FacebookProductOperationKind | None = None
    trigger_action_kind: FacebookActionKind | None = None
    recovery_recipe_kind: FacebookRecoveryRecipeKind = FacebookRecoveryRecipeKind.NONE
    target_id: str | None = None
    policy_delay_seconds: int = 0
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class FacebookAdmissionDecision:
    """Normal work admission decision。"""

    outcome: FacebookAdmissionOutcome
    state: FacebookAccessCircuitSnapshot
    token: FacebookAdmissionToken | None = None


@dataclass(frozen=True)
class FacebookCircuitTripResult:
    """Circuit trip CAS 結果。"""

    outcome: FacebookCircuitTripOutcome
    state: FacebookAccessCircuitSnapshot


@dataclass(frozen=True)
class FacebookSafetyHoldResult:
    """不冒充 Facebook block evidence 的 durable safety hold 結果。"""

    outcome: FacebookSafetyHoldOutcome
    state: FacebookAccessCircuitSnapshot


@dataclass(frozen=True)
class FacebookProbeRequestResult:
    """Persistent probe request 結果。"""

    outcome: FacebookProbeRequestOutcome
    state: FacebookAccessCircuitSnapshot


@dataclass(frozen=True)
class FacebookHalfOpenClaimResult:
    """Half-open claim 結果與本次 probe recipe snapshot。"""

    outcome: FacebookHalfOpenClaimOutcome
    state: FacebookAccessCircuitSnapshot | None
    recipe_kind: FacebookRecoveryRecipeKind = FacebookRecoveryRecipeKind.NONE
    target_id: str | None = None


@dataclass(frozen=True)
class FacebookProbeFinishResult:
    """Half-open finish CAS 結果。"""

    outcome: FacebookProbeFinishOutcome
    state: FacebookAccessCircuitSnapshot | None


@dataclass(frozen=True)
class FacebookLeaseRecoveryResult:
    """Expired half-open lease recovery 結果。"""

    outcome: FacebookLeaseRecoveryOutcome
    state: FacebookAccessCircuitSnapshot | None


__all__ = [
    "FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON",
    "FACEBOOK_TEMPORARY_BLOCK_REASON",
    "FacebookAccessBlockSignal",
    "FacebookAccessCircuitEvent",
    "FacebookAccessCircuitSnapshot",
    "FacebookAccessCircuitStatus",
    "FacebookAccessEventKind",
    "FacebookAccessSignalConfidence",
    "FacebookActionKind",
    "FacebookAdmissionDecision",
    "FacebookAdmissionOutcome",
    "FacebookAdmissionToken",
    "FacebookCircuitTripOutcome",
    "FacebookCircuitTripResult",
    "FacebookHalfOpenClaimOutcome",
    "FacebookHalfOpenClaimResult",
    "FacebookLeaseRecoveryOutcome",
    "FacebookLeaseRecoveryResult",
    "FacebookProbeFinishOutcome",
    "FacebookProbeFinishResult",
    "FacebookProbeRequestOutcome",
    "FacebookProbeRequestResult",
    "FacebookProbeResult",
    "FacebookProductOperationKind",
    "FacebookRecoveryRecipeKind",
    "FacebookSafetyHoldOutcome",
    "FacebookSafetyHoldResult",
    "FacebookWorkSourceKind",
]
