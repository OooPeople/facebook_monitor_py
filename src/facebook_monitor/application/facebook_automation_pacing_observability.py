"""Persistent Facebook automation pacing 的 privacy-safe 可觀測投影。

職責：以唯讀 SQLite 取得目前 managed profile 的 pacing row，
只輸出 bounded work/outcome code 與時間，不回傳 operation、session 或 raw profile key。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from facebook_monitor.application.managed_profile_identity import (
    inspect_managed_profile_identity,
)
from facebook_monitor.core.facebook_automation_pacing import (
    FacebookAutomationPacingSnapshot,
)
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.facebook_automation_pacing import (
    FacebookAutomationPacingRepository,
)


_SAFE_WORK_KINDS = frozenset(
    {
        "half_open_probe",
        "target_scan",
        "metadata_refresh",
        "cover_refresh",
    }
)
_SAFE_LAST_OUTCOMES = frozenset(
    {
        "started",
        "finished",
        "success",
        "blocked",
        "inconclusive",
        "cancelled",
        "recovered_expired",
    }
)


@dataclass(frozen=True)
class FacebookAutomationPacingSafeSnapshot:
    """不含 persistent owner identity 的 pacing 摘要。"""

    available: bool = False
    profile_scope: str = ""
    active: bool = False
    active_work_kind: str = ""
    active_lease_expires_at: str = ""
    active_lease_expired: bool = False
    quiet_period_active: bool = False
    next_automation_not_before: str = ""
    last_automation_started_at: str = ""
    last_automation_finished_at: str = ""
    last_outcome: str = ""


def read_existing_facebook_automation_pacing(
    *,
    db_path: Path,
    profile_dir: Path,
) -> FacebookAutomationPacingSnapshot | None:
    """以既有 profile marker 與唯讀 DB 取得 pacing，不建立任何狀態。"""

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
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("facebook_automation_pacing_state",),
        ).fetchone()
        if table is None:
            return None
        return FacebookAutomationPacingRepository(connection).get(
            identity.profile_scope_key
        )
    finally:
        connection.close()


def build_facebook_automation_pacing_safe_snapshot(
    snapshot: FacebookAutomationPacingSnapshot | None,
    *,
    profile_scope: str,
    now: datetime | None = None,
) -> FacebookAutomationPacingSafeSnapshot:
    """將 persistent owner state 投影為 bounded diagnostics 欄位。"""

    if snapshot is None:
        return FacebookAutomationPacingSafeSnapshot()
    observed_at = now or utc_now()
    lease_expires_at = snapshot.active_lease_expires_at
    next_not_before = snapshot.next_automation_not_before
    return FacebookAutomationPacingSafeSnapshot(
        available=True,
        profile_scope=profile_scope,
        active=bool(snapshot.active_operation_id),
        active_work_kind=safe_facebook_automation_work_kind(
            snapshot.active_work_kind
        ),
        active_lease_expires_at=_timestamp(lease_expires_at),
        active_lease_expired=bool(
            snapshot.active_operation_id
            and lease_expires_at is not None
            and lease_expires_at <= observed_at
        ),
        quiet_period_active=bool(
            not snapshot.active_operation_id
            and next_not_before is not None
            and next_not_before > observed_at
        ),
        next_automation_not_before=_timestamp(next_not_before),
        last_automation_started_at=_timestamp(snapshot.last_automation_started_at),
        last_automation_finished_at=_timestamp(snapshot.last_automation_finished_at),
        last_outcome=_safe_code(snapshot.last_outcome, _SAFE_LAST_OUTCOMES),
    )


def safe_facebook_automation_work_kind(value: str) -> str:
    """只保留已知 coordinator/pacing work kind。"""

    return _safe_code(value, _SAFE_WORK_KINDS)


def _safe_code(value: str, allowed: frozenset[str]) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return normalized if normalized in allowed else "unrecognized_code"


def _timestamp(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


__all__ = [
    "FacebookAutomationPacingSafeSnapshot",
    "build_facebook_automation_pacing_safe_snapshot",
    "read_existing_facebook_automation_pacing",
    "safe_facebook_automation_work_kind",
]
