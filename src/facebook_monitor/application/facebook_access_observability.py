"""Facebook access circuit 的 privacy-safe 可觀測摘要。

職責：唯讀取得目前 managed profile 的 circuit truth，並只投影 Web UI、
runtime diagnostics 與 support bundle 可安全共用的 bounded 欄位。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from facebook_monitor.application.facebook_access_circuit_service import (
    FacebookAccessCircuitService,
)
from facebook_monitor.application.facebook_session_recovery_service import (
    FacebookSessionRecoveryService,
)
from facebook_monitor.application.managed_profile_identity import (
    inspect_managed_profile_identity,
)
from facebook_monitor.application.managed_profile_identity import (
    ManagedProfileIdentityStatus,
)
from facebook_monitor.core.facebook_access import FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON
from facebook_monitor.core.facebook_access import FacebookAccessCircuitSnapshot
from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookProbeRequestOutcome
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.persistence.repositories.facebook_access_circuit import (
    FacebookAccessCircuitRepository,
)
from facebook_monitor.persistence.repositories.facebook_automation_pacing import (
    FacebookAutomationPacingRepository,
)
from facebook_monitor.persistence.repositories.facebook_session_recovery import (
    FacebookSessionRecoveryRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


_SAFE_CIRCUIT_REASONS = frozenset(
    {
        FACEBOOK_ACCESS_PERSISTENCE_UNCERTAIN_REASON,
        FACEBOOK_TEMPORARY_BLOCK_REASON,
    }
)


@dataclass(frozen=True)
class FacebookAccessSafeSnapshot:
    """不含 raw profile key、target、episode、token 或 URL 的 circuit 摘要。"""

    available: bool = False
    profile_scope: str = ""
    state: str = "unknown"
    reason: str = ""
    cooldown_active: bool = False
    cooldown_until: str = ""
    probe_pending: bool = False
    last_probe_result: str = ""
    recovery_available: bool = False
    recovery_disabled_reason: str = ""


@dataclass(frozen=True)
class ExistingFacebookAccessObservation:
    """保存唯讀 circuit 與同一 DB snapshot 算出的 probe readiness。"""

    circuit: FacebookAccessCircuitSnapshot | None = None
    probe_request_outcome: FacebookProbeRequestOutcome = (
        FacebookProbeRequestOutcome.REJECTED_STATE
    )
    identity_status: str = ManagedProfileIdentityStatus.UNINITIALIZED.value


@dataclass(frozen=True)
class FacebookSessionRecoverySafeDiagnostics:
    """不含scope、marker、request、target或probe owner的recovery摘要。"""

    available: bool = False
    status: str = "unknown"
    earliest_readiness_at: str = ""
    quiet_period_active: bool = False
    probe_state: str = "idle"
    last_result: str = ""


def read_existing_facebook_access_circuit(
    *,
    db_path: Path,
    profile_dir: Path,
) -> FacebookAccessCircuitSnapshot | None:
    """用既有 marker 與唯讀 SQLite 連線取得目前 profile circuit。"""

    return read_existing_facebook_access_observation(
        db_path=db_path,
        profile_dir=profile_dir,
    ).circuit


def read_existing_facebook_access_observation(
    *,
    db_path: Path,
    profile_dir: Path,
    now: datetime | None = None,
) -> ExistingFacebookAccessObservation:
    """唯讀取得目前 circuit 與 trigger target 的 recovery readiness。"""

    inspection = inspect_managed_profile_identity(
        db_path=db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    identity = inspection.identity
    if identity is None or not db_path.is_file():
        return ExistingFacebookAccessObservation(
            identity_status=inspection.status.value,
        )
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 500")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("facebook_access_circuit_state",),
        ).fetchone()
        if table is None:
            return ExistingFacebookAccessObservation(
                identity_status=inspection.status.value,
            )
        circuit_repository = FacebookAccessCircuitRepository(connection)
        circuit = circuit_repository.get(identity.profile_scope_key)
        if circuit is None:
            return ExistingFacebookAccessObservation(
                identity_status=inspection.status.value,
            )
        readiness = FacebookAccessCircuitService(
            circuit_repository,
            TargetRepository(connection),
        ).probe_request_readiness(
            identity.profile_scope_key,
            target_id=None,
            checked_at=now or utc_now(),
        )
        return ExistingFacebookAccessObservation(
            circuit=circuit,
            probe_request_outcome=readiness,
            identity_status=inspection.status.value,
        )
    finally:
        connection.close()


def read_existing_facebook_session_recovery_safe_snapshot(
    *,
    db_path: Path,
    profile_dir: Path,
    now: datetime | None = None,
) -> FacebookAccessSafeSnapshot | None:
    """投影 stale normal-session recovery，不回傳 marker/owner/target identity。"""

    inspection = inspect_managed_profile_identity(
        db_path=db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    identity = inspection.identity
    if identity is None or not db_path.is_file():
        return None
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 500")
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required_tables = {
            "facebook_session_recovery_state",
            "facebook_automation_pacing_state",
            "targets",
        }
        if not required_tables.issubset(tables):
            return None
        service = FacebookSessionRecoveryService(
            FacebookSessionRecoveryRepository(connection),
            FacebookAutomationPacingRepository(connection),
            TargetRepository(connection),
        )
        state = service.get(identity.profile_scope_key)
        if state is None:
            return None
        if state.status == FacebookSessionRecoveryStatus.RECOVERED:
            return FacebookAccessSafeSnapshot(
                available=True,
                profile_scope="managed_profile",
                state="storage_critical",
                reason="storage_critical",
                recovery_disabled_reason="storage_critical",
            )
        observed_at = now or utc_now()
        has_candidate = any(
            service.list_probe_target_candidates(
                identity.profile_scope_key,
                operation_kind=operation_kind,
            )
            for operation_kind in (
                FacebookProductOperationKind.POSTS_ACCESS,
                FacebookProductOperationKind.GROUP_METADATA_ACCESS,
                FacebookProductOperationKind.COVER_METADATA_ACCESS,
            )
        )
        quiet_active = observed_at < state.earliest_probe_at
        pending = state.status in {
            FacebookSessionRecoveryStatus.PROBE_PENDING,
            FacebookSessionRecoveryStatus.PROBING,
        }
        recovery_available = bool(
            state.status == FacebookSessionRecoveryStatus.HOLD
            and not quiet_active
            and has_candidate
        )
        if state.status == FacebookSessionRecoveryStatus.PROBE_PENDING:
            disabled_reason = "probe_pending"
        elif state.status == FacebookSessionRecoveryStatus.PROBING:
            disabled_reason = "probe_in_progress"
        elif quiet_active:
            disabled_reason = "unclean_session_quiet_period"
        elif not has_candidate:
            disabled_reason = "unclean_session_target_unavailable"
        else:
            disabled_reason = ""
        return FacebookAccessSafeSnapshot(
            available=True,
            profile_scope="managed_profile",
            state="unclean_session_hold",
            reason="facebook_automation_unclean_session",
            cooldown_active=quiet_active,
            cooldown_until=state.earliest_probe_at.isoformat(),
            probe_pending=pending,
            last_probe_result=state.last_probe_result.value,
            recovery_available=recovery_available,
            recovery_disabled_reason=disabled_reason,
        )
    finally:
        connection.close()


def read_existing_facebook_session_recovery_safe_diagnostics(
    *,
    db_path: Path,
    profile_dir: Path,
    now: datetime | None = None,
) -> FacebookSessionRecoverySafeDiagnostics:
    """唯讀投影bounded recovery diagnostics，刻意排除所有durable identities。"""

    inspection = inspect_managed_profile_identity(
        db_path=db_path,
        profiles_root=profile_dir.parent,
        profile_dir=profile_dir,
    )
    identity = inspection.identity
    if identity is None or not db_path.is_file():
        return FacebookSessionRecoverySafeDiagnostics()
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 500")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("facebook_session_recovery_state",),
        ).fetchone()
        if table is None:
            return FacebookSessionRecoverySafeDiagnostics()
        state = FacebookSessionRecoveryRepository(connection).get(
            identity.profile_scope_key
        )
        if state is None:
            return FacebookSessionRecoverySafeDiagnostics()
        observed_at = now or utc_now()
        probe_state = {
            FacebookSessionRecoveryStatus.PROBE_PENDING: "pending",
            FacebookSessionRecoveryStatus.PROBING: "probing",
        }.get(state.status, "idle")
        return FacebookSessionRecoverySafeDiagnostics(
            available=True,
            status=state.status.value,
            earliest_readiness_at=state.earliest_probe_at.isoformat(),
            quiet_period_active=bool(
                state.status == FacebookSessionRecoveryStatus.HOLD
                and observed_at < state.earliest_probe_at
            ),
            probe_state=probe_state,
            last_result=state.last_probe_result.value,
        )
    finally:
        connection.close()


def build_facebook_access_safe_snapshot(
    snapshot: FacebookAccessCircuitSnapshot | None,
    *,
    profile_scope: str,
    now: datetime | None = None,
    probe_request_outcome: FacebookProbeRequestOutcome | None = None,
    runtime_hold: str = "",
) -> FacebookAccessSafeSnapshot:
    """將 domain snapshot 投影成固定 allowlist 的安全摘要。"""

    normalized_hold = str(runtime_hold).strip()
    if normalized_hold in {"unclean_session_hold", "storage_critical"}:
        return FacebookAccessSafeSnapshot(
            available=True,
            profile_scope=profile_scope,
            state=normalized_hold,
            reason=normalized_hold,
            recovery_disabled_reason=(
                "unclean_session_healthcheck_unavailable"
                if normalized_hold == "unclean_session_hold"
                else "storage_critical"
            ),
        )
    if snapshot is None:
        return FacebookAccessSafeSnapshot()
    observed_at = now or utc_now()
    cooldown_until = snapshot.cooldown_until
    safe_reason = _safe_reason(snapshot.reason_code)
    return FacebookAccessSafeSnapshot(
        available=True,
        profile_scope=profile_scope,
        state=snapshot.status.value,
        reason=safe_reason,
        cooldown_active=bool(cooldown_until is not None and cooldown_until > observed_at),
        cooldown_until=(cooldown_until.isoformat() if cooldown_until is not None else ""),
        probe_pending=bool(snapshot.probe_request_id),
        last_probe_result=snapshot.last_probe_result.value,
        recovery_available=(
            probe_request_outcome == FacebookProbeRequestOutcome.REQUESTED
        ),
        recovery_disabled_reason=_recovery_disabled_reason(
            snapshot,
            probe_request_outcome,
        ),
    )


def _safe_reason(value: str) -> str:
    """只保留 circuit 定義的 stable reason code。"""

    normalized = str(value or "").strip()
    if normalized in _SAFE_CIRCUIT_REASONS:
        return normalized
    return "unrecognized_code" if normalized else ""


def _recovery_disabled_reason(
    snapshot: FacebookAccessCircuitSnapshot,
    outcome: FacebookProbeRequestOutcome | None,
) -> str:
    """將 readiness 轉成不含 identity 的 stable UI reason。"""

    if outcome == FacebookProbeRequestOutcome.REQUESTED:
        return ""
    if snapshot.status == FacebookAccessCircuitStatus.HALF_OPEN:
        return "probe_in_progress"
    if outcome == FacebookProbeRequestOutcome.ALREADY_PENDING:
        return "probe_pending"
    if outcome == FacebookProbeRequestOutcome.COOLDOWN_ACTIVE:
        return "cooldown_active"
    if outcome == FacebookProbeRequestOutcome.RECIPE_UNAVAILABLE:
        if snapshot.operation_kind == FacebookProductOperationKind.COMMENTS_ACCESS:
            return "comments_recovery_recipe_unavailable"
        return "recovery_recipe_unavailable"
    if outcome == FacebookProbeRequestOutcome.TARGET_UNAVAILABLE:
        return "trigger_target_unavailable"
    if outcome == FacebookProbeRequestOutcome.REJECTED_STATE:
        return "circuit_state_not_open"
    return "recovery_readiness_unavailable"


def circuit_requires_global_pause(snapshot: FacebookAccessSafeSnapshot) -> bool:
    """判斷安全摘要是否代表 profile-wide automation pause。"""

    return snapshot.available and snapshot.state in {
        FacebookAccessCircuitStatus.OPEN.value,
        FacebookAccessCircuitStatus.HALF_OPEN.value,
        "unclean_session_hold",
        "storage_critical",
    }


__all__ = [
    "ExistingFacebookAccessObservation",
    "FacebookAccessSafeSnapshot",
    "FacebookSessionRecoverySafeDiagnostics",
    "build_facebook_access_safe_snapshot",
    "circuit_requires_global_pause",
    "read_existing_facebook_access_circuit",
    "read_existing_facebook_access_observation",
    "read_existing_facebook_session_recovery_safe_diagnostics",
    "read_existing_facebook_session_recovery_safe_snapshot",
]
