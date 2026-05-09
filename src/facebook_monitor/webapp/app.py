"""FastAPI local management UI assembly。

職責：建立 FastAPI app、掛載 static/templates、管理 lifespan 與註冊 route modules。
實際 route handler 分散於 `webapp.routes.*`。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.core.models import utc_now
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.webapp.dependencies import DEFAULT_DB_PATH
from facebook_monitor.webapp.dependencies import DEFAULT_PROFILE_DIR
from facebook_monitor.webapp.dependencies import STATIC_DIR
from facebook_monitor.webapp.dependencies import TEMPLATES_DIR
from facebook_monitor.webapp.dependencies import build_scheduler_options
from facebook_monitor.webapp.dependencies import default_group_name_resolver
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_global_notification_settings
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_new_target_with_message
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.webapp.form_models import parse_keywords_text
from facebook_monitor.webapp.profile_session import ProfileSessionManager
from facebook_monitor.webapp.routes.dashboard import register_dashboard_routes
from facebook_monitor.webapp.routes.hit_records import register_hit_record_routes
from facebook_monitor.webapp.routes.scheduler import register_scheduler_routes
from facebook_monitor.webapp.routes.settings import register_settings_routes
from facebook_monitor.webapp.routes.targets import register_target_routes
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    profile_manager: ProfileSessionManager | None = None,
    group_name_resolver: Callable[[Path, str], str] | None = None,
    scheduler_manager: BackgroundSchedulerManager | None = None,
    auto_start_scheduler: bool = False,
    scheduler_interval_seconds: float = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
    scheduler_tick_seconds: float = 2,
    max_concurrent_scans: int = 2,
    reset_targets_on_startup: bool = False,
    reset_runtime_data_on_startup: bool = False,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
) -> FastAPI:
    """建立 FastAPI app，供 uvicorn 或測試使用。"""

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> object:
        """管理 Web UI 啟動與關閉時的背景 scheduler 生命週期。"""

        with SqliteApplicationContext(app_instance.state.db_path) as app_context:
            app_context.repositories.match_history.prune_global_limit()
        if app_instance.state.reset_runtime_data_on_startup:
            with SqliteApplicationContext(app_instance.state.db_path) as app_context:
                app_context.repositories.maintenance.clear_runtime_data()
        if app_instance.state.reset_targets_on_startup:
            with SqliteApplicationContext(app_instance.state.db_path) as app_context:
                app_context.services.targets.pause_all_targets_for_webui_startup(
                    default_fixed_refresh_sec=app_instance.state.scheduler_interval_seconds,
                )
        if app_instance.state.auto_start_scheduler:
            app_instance.state.scheduler_manager.start(
                SchedulerSessionOptions(
                    db_path=app_instance.state.db_path,
                    profile_dir=app_instance.state.profile_dir,
                    interval_seconds=app_instance.state.scheduler_interval_seconds,
                    scheduler_tick_seconds=app_instance.state.scheduler_tick_seconds,
                    max_concurrent_scans=app_instance.state.max_concurrent_scans,
                )
            )
        try:
            yield
        finally:
            app_instance.state.scheduler_manager.stop()

    app = FastAPI(title="Facebook Monitor Local UI", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.profile_dir = profile_dir
    app.state.profile_manager = profile_manager or ProfileSessionManager()
    app.state.group_name_resolver = group_name_resolver or default_group_name_resolver
    app.state.scheduler_manager = scheduler_manager or BackgroundSchedulerManager()
    app.state.auto_start_scheduler = auto_start_scheduler
    app.state.scheduler_interval_seconds = scheduler_interval_seconds
    app.state.scheduler_tick_seconds = scheduler_tick_seconds
    app.state.max_concurrent_scans = max_concurrent_scans
    app.state.session_started_at = utc_now()
    app.state.reset_targets_on_startup = reset_targets_on_startup
    app.state.reset_runtime_data_on_startup = reset_runtime_data_on_startup
    app.state.scheduler_paused_for_profile = False
    app.state.scheduler_resume_options = None
    app.state.ntfy_sender = ntfy_sender
    app.state.desktop_sender = desktop_sender
    app.state.discord_sender = discord_sender
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    register_dashboard_routes(app, templates)
    register_hit_record_routes(app)
    register_target_routes(app, templates)
    register_settings_routes(app, templates)
    register_scheduler_routes(app)
    return app


app = create_app(
    auto_start_scheduler=True,
    reset_targets_on_startup=True,
    reset_runtime_data_on_startup=True,
)


__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_PROFILE_DIR",
    "build_scheduler_options",
    "create_app",
    "get_db_path",
    "get_desktop_sender",
    "get_discord_sender",
    "get_global_notification_settings",
    "get_group_name_resolver",
    "get_ntfy_sender",
    "get_profile_dir",
    "get_profile_manager",
    "get_scheduler_manager",
    "get_session_started_at",
    "parse_keywords_text",
    "pause_scheduler_for_profile_use",
    "redirect_new_target_with_error",
    "redirect_new_target_with_message",
    "redirect_settings_with_error",
    "redirect_settings_with_message",
    "redirect_with_error",
    "redirect_with_message",
    "resume_scheduler_after_profile_use",
    "run_with_temporary_profile_access",
]
