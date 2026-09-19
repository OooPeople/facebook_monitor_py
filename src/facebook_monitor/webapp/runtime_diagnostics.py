"""Runtime diagnostics presenter。

職責：整理設定頁可顯示與複製的 app runtime 診斷資訊，
避免 template 直接讀取 app.state 或自行拼接路徑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any

from facebook_monitor.application.facebook_access_observability import (
    build_facebook_access_safe_snapshot,
)
from facebook_monitor.application.facebook_access_observability import (
    read_existing_facebook_access_observation,
)
from facebook_monitor.application.facebook_automation_runtime_observability import (
    read_facebook_automation_runtime_hold,
)
from facebook_monitor.application.facebook_automation_pacing_observability import (
    build_facebook_automation_pacing_safe_snapshot,
)
from facebook_monitor.application.facebook_automation_pacing_observability import (
    read_existing_facebook_automation_pacing,
)
from facebook_monitor.application.facebook_automation_pacing_observability import (
    safe_facebook_automation_work_kind,
)
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.webapp.assets import ASSET_VERSION


@dataclass(frozen=True)
class RuntimeDiagnosticField:
    """單筆 runtime diagnostic 顯示欄位。"""

    label: str
    value: str


@dataclass(frozen=True)
class RuntimeDiagnosticsView:
    """設定頁 runtime diagnostics view model。"""

    fields: tuple[RuntimeDiagnosticField, ...]
    copy_text: str


def build_runtime_diagnostics_view(app_state: Any) -> RuntimeDiagnosticsView:
    """依目前 FastAPI app state 建立 runtime diagnostics view。"""

    paths = getattr(app_state, "runtime_paths", None)
    if not isinstance(paths, RuntimePaths):
        paths = default_runtime_paths()
    db_path = Path(getattr(app_state, "db_path", paths.db_path))
    profile_dir = Path(getattr(app_state, "profile_dir", paths.profile_dir))
    templates_dir = Path(getattr(app_state, "templates_dir", paths.templates_dir))
    static_dir = Path(getattr(app_state, "static_dir", paths.static_dir))
    metadata = collect_build_metadata(asset_version=ASSET_VERSION)
    reset_targets_on_startup = bool(getattr(app_state, "reset_targets_on_startup", False))
    resume_active_targets_on_startup = bool(
        getattr(app_state, "resume_active_targets_on_startup", False)
    )
    reset_runtime_data_on_startup = bool(
        getattr(app_state, "reset_runtime_data_on_startup", False)
    )
    scheduler_state = _scheduler_state_text(getattr(app_state, "scheduler_manager", None))
    facebook_access_circuit = _facebook_access_circuit_text(
        db_path=db_path,
        profile_dir=profile_dir,
    )
    facebook_automation_pacing = _facebook_automation_pacing_text(
        db_path=db_path,
        profile_dir=profile_dir,
    )
    fields = (
        RuntimeDiagnosticField("App", metadata.app_name),
        RuntimeDiagnosticField("Version", metadata.app_version),
        RuntimeDiagnosticField("Asset version", metadata.asset_version),
        RuntimeDiagnosticField("Python version", metadata.python_version),
        RuntimeDiagnosticField("Executable", str(metadata.executable)),
        RuntimeDiagnosticField("Frozen", str(metadata.frozen).lower()),
        RuntimeDiagnosticField("Packaging mode", metadata.packaging_mode),
        RuntimeDiagnosticField("Build date", metadata.build_date),
        RuntimeDiagnosticField("Git commit", metadata.git_commit),
        RuntimeDiagnosticField("DB path", str(db_path)),
        RuntimeDiagnosticField("Profile dir", str(profile_dir)),
        RuntimeDiagnosticField("Data dir", str(paths.data_dir)),
        RuntimeDiagnosticField("Logs dir", str(paths.logs_dir)),
        RuntimeDiagnosticField("Runtime dir", str(paths.runtime_dir)),
        RuntimeDiagnosticField("Updates dir", str(paths.updates_dir)),
        RuntimeDiagnosticField("Templates dir", str(templates_dir)),
        RuntimeDiagnosticField("Static dir", str(static_dir)),
        RuntimeDiagnosticField("Browser mode", "playwright_chromium"),
        RuntimeDiagnosticField(
            "Reset targets on startup",
            str(reset_targets_on_startup).lower(),
        ),
        RuntimeDiagnosticField(
            "Resume active targets on startup",
            str(resume_active_targets_on_startup).lower(),
        ),
        RuntimeDiagnosticField(
            "Reset runtime data on startup",
            str(reset_runtime_data_on_startup).lower(),
        ),
        RuntimeDiagnosticField("Scheduler", scheduler_state),
        RuntimeDiagnosticField("Facebook access circuit", facebook_access_circuit),
        RuntimeDiagnosticField("Facebook automation pacing", facebook_automation_pacing),
    )
    return RuntimeDiagnosticsView(
        fields=fields,
        copy_text="\n".join(f"{field.label}: {field.value}" for field in fields),
    )


def _scheduler_state_text(scheduler_manager: Any) -> str:
    """整理 scheduler runtime state，不呼叫任何啟停操作。"""

    if scheduler_manager is None:
        return "unknown"
    try:
        state = scheduler_manager.state()
    except Exception:
        return "unavailable"
    running = "running" if getattr(state, "running", False) else "stopped"
    queued = getattr(state, "current_queued_count", 0)
    active = getattr(state, "current_running_count", 0)
    slots = getattr(state, "max_concurrent_scans", 0)
    coordinator_active = bool(
        getattr(state, "automation_coordinator_active", False)
    )
    coordinator_work = safe_facebook_automation_work_kind(
        str(getattr(state, "automation_coordinator_work_kind", ""))
    )
    coordinator_waiters = max(
        int(getattr(state, "automation_coordinator_waiter_count", 0)),
        0,
    )
    return (
        f"{running}; running={active}; queued={queued}; slots={slots}; "
        f"coordinator_active={str(coordinator_active).lower()}; "
        f"coordinator_work={coordinator_work or 'none'}; "
        f"coordinator_waiters={coordinator_waiters}"
    )


def _facebook_access_circuit_text(*, db_path: Path, profile_dir: Path) -> str:
    """整理不含 raw profile key 或 target identity 的 circuit diagnostics。"""

    try:
        observation = read_existing_facebook_access_observation(
            db_path=db_path,
            profile_dir=profile_dir,
        )
        runtime_hold = read_facebook_automation_runtime_hold(
            db_path=db_path,
            profile_dir=profile_dir,
            browser_session_active=False,
        )
    except (OSError, sqlite3.Error, ValueError):
        return "unavailable"
    snapshot = build_facebook_access_safe_snapshot(
        observation.circuit,
        profile_scope="managed_profile",
        probe_request_outcome=observation.probe_request_outcome,
        runtime_hold=runtime_hold,
    )
    if not snapshot.available:
        return "unavailable"
    cooldown = "active" if snapshot.cooldown_active else "inactive"
    return (
        f"profile_scope={snapshot.profile_scope}; state={snapshot.state}; "
        f"reason={snapshot.reason}; cooldown={cooldown}; "
        f"cooldown_until={snapshot.cooldown_until or 'none'}"
    )


def _facebook_automation_pacing_text(*, db_path: Path, profile_dir: Path) -> str:
    """整理不含 operation/session/profile key 的 persistent pacing 狀態。"""

    try:
        pacing = read_existing_facebook_automation_pacing(
            db_path=db_path,
            profile_dir=profile_dir,
        )
    except (OSError, sqlite3.Error, ValueError):
        return "unavailable"
    snapshot = build_facebook_automation_pacing_safe_snapshot(
        pacing,
        profile_scope="managed_profile",
    )
    if not snapshot.available:
        return "unavailable"
    return (
        f"profile_scope={snapshot.profile_scope}; active={str(snapshot.active).lower()}; "
        f"work_kind={snapshot.active_work_kind or 'none'}; "
        f"lease_expires_at={snapshot.active_lease_expires_at or 'none'}; "
        f"lease_expired={str(snapshot.active_lease_expired).lower()}; "
        f"quiet_period_active={str(snapshot.quiet_period_active).lower()}; "
        f"next_not_before={snapshot.next_automation_not_before or 'none'}; "
        f"last_started_at={snapshot.last_automation_started_at or 'none'}; "
        f"last_finished_at={snapshot.last_automation_finished_at or 'none'}; "
        f"last_outcome={snapshot.last_outcome or 'none'}"
    )
