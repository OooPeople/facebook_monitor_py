"""Runtime diagnostics presenter。

職責：整理設定頁可顯示與複製的 app runtime 診斷資訊，
避免 template 直接讀取 app.state 或自行拼接路徑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    return f"{running}; running={active}; queued={queued}; slots={slots}"
