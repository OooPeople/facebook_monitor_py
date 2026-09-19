"""Persistent Facebook automation pacing domain types。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class FacebookPacingAcquireOutcome(StrEnum):
    """Persistent pacing lease acquisition 結果。"""

    ACQUIRED = "acquired"
    QUIET_PERIOD = "quiet_period"
    ACTIVE_LEASE = "active_lease"


class FacebookPacingFinishOutcome(StrEnum):
    """Persistent pacing lease finish 結果。"""

    UPDATED = "updated"
    STALE_OWNER = "stale_owner"


class FacebookPacingRecoveryOutcome(StrEnum):
    """Expired pacing lease recovery 結果。"""

    RECOVERED = "recovered"
    NOT_FOUND = "not_found"
    NOT_ACTIVE = "not_active"
    NOT_EXPIRED = "not_expired"
    OWNER_MISMATCH = "owner_mismatch"


@dataclass(frozen=True)
class FacebookAutomationPacingSnapshot:
    """單一 managed profile 的跨 restart pacing 狀態。"""

    profile_scope_key: str
    lease_generation: int = 0
    active_operation_id: str = ""
    active_work_kind: str = ""
    owner_session_id: str = ""
    active_lease_expires_at: datetime | None = None
    last_automation_started_at: datetime | None = None
    last_automation_finished_at: datetime | None = None
    next_automation_not_before: datetime | None = None
    last_outcome: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True)
class FacebookAutomationPacingToken:
    """綁定 narrow finish CAS 的 persistent pacing owner token。"""

    profile_scope_key: str
    lease_generation: int
    operation_id: str
    owner_session_id: str


@dataclass(frozen=True)
class FacebookPacingAcquireResult:
    """Persistent pacing acquisition 結果。"""

    outcome: FacebookPacingAcquireOutcome
    state: FacebookAutomationPacingSnapshot
    token: FacebookAutomationPacingToken | None = None


@dataclass(frozen=True)
class FacebookPacingFinishResult:
    """Persistent pacing finish 結果。"""

    outcome: FacebookPacingFinishOutcome
    state: FacebookAutomationPacingSnapshot


@dataclass(frozen=True)
class FacebookPacingRecoveryResult:
    """Expired pacing lease recovery 結果。"""

    outcome: FacebookPacingRecoveryOutcome
    state: FacebookAutomationPacingSnapshot | None


__all__ = [
    "FacebookAutomationPacingSnapshot",
    "FacebookAutomationPacingToken",
    "FacebookPacingAcquireOutcome",
    "FacebookPacingAcquireResult",
    "FacebookPacingFinishOutcome",
    "FacebookPacingFinishResult",
    "FacebookPacingRecoveryOutcome",
    "FacebookPacingRecoveryResult",
]
