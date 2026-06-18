"""Web UI FastAPI app.state wiring。

職責：集中 app factory 注入的 managers、senders、runtime flags 與安全設定。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from facebook_monitor.application.maintenance import run_bounded_retention_maintenance_for_db
from facebook_monitor.core.models import utc_now
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender
from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.webapp.dependencies import default_group_name_resolver
from facebook_monitor.webapp.dependencies import GroupMetadataResolver
from facebook_monitor.webapp.dashboard_revision_notifier import DashboardRevisionNotifier
from facebook_monitor.webapp.dashboard_revision_query import get_dashboard_revision
from facebook_monitor.webapp.maintenance import BoundedRetentionMaintenanceRunner
from facebook_monitor.webapp.profile_session import ProfileManagerLike
from facebook_monitor.webapp.profile_session import ProfileSessionManager
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerManagerLike


@dataclass(frozen=True)
class WebAppStateConfig:
    """保存 `create_app()` 寫入 app.state 的設定。"""

    db_path: Path
    profile_dir: Path
    templates_dir: Path
    static_dir: Path
    profile_manager: ProfileManagerLike | None
    group_name_resolver: GroupMetadataResolver | None
    scheduler_manager: SchedulerManagerLike | None
    auto_start_scheduler: bool
    scheduler_interval_seconds: float
    scheduler_tick_seconds: float
    max_concurrent_scans: int
    reset_targets_on_startup: bool
    reset_runtime_data_on_startup: bool
    ntfy_sender: NtfySender
    desktop_sender: DesktopSender
    discord_sender: DiscordSender
    csrf_token: str
    enforce_csrf: bool
    max_request_body_bytes: int


def configure_app_state(app: FastAPI, config: WebAppStateConfig) -> None:
    """依 factory config 寫入 Web UI routes 與 lifespan 依賴的 app.state。"""

    app.state.db_path = config.db_path
    app.state.profile_dir = config.profile_dir
    app.state.templates_dir = config.templates_dir
    app.state.static_dir = config.static_dir
    app.state.profile_manager = config.profile_manager or ProfileSessionManager()
    app.state.bounded_retention_maintenance_runner = BoundedRetentionMaintenanceRunner(
        run_bounded_retention_maintenance_for_db
    )
    app.state.group_name_resolver = (
        config.group_name_resolver or default_group_name_resolver
    )
    app.state.scheduler_manager = config.scheduler_manager or BackgroundSchedulerManager()
    app.state.auto_start_scheduler = config.auto_start_scheduler
    app.state.scheduler_interval_seconds = config.scheduler_interval_seconds
    app.state.scheduler_tick_seconds = config.scheduler_tick_seconds
    app.state.max_concurrent_scans = config.max_concurrent_scans
    app.state.sse_poll_interval_seconds = (
        PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_poll_interval_seconds
    )
    app.state.sse_keepalive_seconds = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_keepalive_seconds
    app.state.sse_retry_milliseconds = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_retry_milliseconds
    app.state.dashboard_revision_notifier = DashboardRevisionNotifier(
        db_path=config.db_path,
        get_dashboard_revision=get_dashboard_revision,
        poll_interval_seconds=PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_poll_interval_seconds,
    )
    app.state.session_started_at = utc_now()
    app.state.reset_targets_on_startup = config.reset_targets_on_startup
    app.state.resume_active_targets_on_startup = False
    app.state.reset_runtime_data_on_startup = config.reset_runtime_data_on_startup
    app.state.scheduler_paused_for_profile = False
    app.state.scheduler_resume_options = None
    app.state.ntfy_sender = config.ntfy_sender
    app.state.desktop_sender = config.desktop_sender
    app.state.discord_sender = config.discord_sender
    app.state.csrf_token = config.csrf_token
    app.state.enforce_csrf = config.enforce_csrf
    app.state.max_request_body_bytes = max(1, int(config.max_request_body_bytes))


__all__ = ["WebAppStateConfig", "configure_app_state"]
