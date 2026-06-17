"""FastAPI local management UI assembly。

職責：建立 FastAPI app，並以明確順序串接 app state、lifespan、middleware、
static resources 與 route modules。實際 request 行為分散於各 owner module。
"""

from __future__ import annotations

from pathlib import Path
from secrets import token_urlsafe

from fastapi import FastAPI

from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core import input_limits
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.version import APP_NAME
from facebook_monitor.version import APP_VERSION
from facebook_monitor.webapp.app_state import configure_app_state
from facebook_monitor.webapp.app_state import WebAppStateConfig
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import DEFAULT_DB_PATH
from facebook_monitor.webapp.dependencies import DEFAULT_PROFILE_DIR
from facebook_monitor.webapp.dependencies import STATIC_DIR
from facebook_monitor.webapp.dependencies import TEMPLATES_DIR
from facebook_monitor.webapp.dependencies import GroupMetadataResolver
from facebook_monitor.webapp.http_security import register_http_security_middleware
from facebook_monitor.webapp.lifespan import webui_lifespan
from facebook_monitor.webapp.maintenance import (
    register_bounded_retention_maintenance_middleware,
)
from facebook_monitor.webapp.profile_session import ProfileManagerLike
from facebook_monitor.webapp.routes.dashboard import register_dashboard_routes
from facebook_monitor.webapp.routes.hit_records import register_hit_record_routes
from facebook_monitor.webapp.routes.settings import register_settings_routes
from facebook_monitor.webapp.routes.sidebar import register_sidebar_routes
from facebook_monitor.webapp.routes.targets import register_target_routes
from facebook_monitor.webapp.scheduler_session import SchedulerManagerLike
from facebook_monitor.webapp.static_files import LocalStaticFiles
from facebook_monitor.webapp.template_env import build_templates


def create_app(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
    templates_dir: Path = TEMPLATES_DIR,
    static_dir: Path = STATIC_DIR,
    profile_manager: ProfileManagerLike | None = None,
    group_name_resolver: GroupMetadataResolver | None = None,
    scheduler_manager: SchedulerManagerLike | None = None,
    auto_start_scheduler: bool = False,
    scheduler_interval_seconds: float = (
        PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec
    ),
    scheduler_tick_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scheduler_tick_seconds,
    max_concurrent_scans: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.max_concurrent_scans,
    reset_targets_on_startup: bool = False,
    reset_runtime_data_on_startup: bool = False,
    ntfy_sender: NtfySender = send_ntfy_notification,
    desktop_sender: DesktopSender = send_desktop_notification,
    discord_sender: DiscordSender = send_discord_notification,
    csrf_token: str | None = None,
    enforce_csrf: bool = True,
    max_request_body_bytes: int = input_limits.MAX_REQUEST_BODY_BYTES,
) -> FastAPI:
    """建立 FastAPI app，供 uvicorn 或測試使用。"""

    csrf_token_value = csrf_token or token_urlsafe(32)
    route_templates = build_templates(templates_dir, csrf_token=csrf_token_value)
    app = FastAPI(title="Facebook Monitor Local UI", lifespan=webui_lifespan)

    configure_app_state(
        app,
        WebAppStateConfig(
            db_path=db_path,
            profile_dir=profile_dir,
            templates_dir=templates_dir,
            static_dir=static_dir,
            profile_manager=profile_manager,
            group_name_resolver=group_name_resolver,
            scheduler_manager=scheduler_manager,
            auto_start_scheduler=auto_start_scheduler,
            scheduler_interval_seconds=scheduler_interval_seconds,
            scheduler_tick_seconds=scheduler_tick_seconds,
            max_concurrent_scans=max_concurrent_scans,
            reset_targets_on_startup=reset_targets_on_startup,
            reset_runtime_data_on_startup=reset_runtime_data_on_startup,
            ntfy_sender=ntfy_sender,
            desktop_sender=desktop_sender,
            discord_sender=discord_sender,
            csrf_token=csrf_token_value,
            enforce_csrf=enforce_csrf,
            max_request_body_bytes=max_request_body_bytes,
        ),
    )
    register_bounded_retention_maintenance_middleware(app)
    app.mount("/static", LocalStaticFiles(directory=str(static_dir)), name="static")
    register_http_security_middleware(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        """回傳 launcher single-instance 檢查用 health payload。"""

        metadata = collect_build_metadata(asset_version=ASSET_VERSION)
        return {
            "status": "ok",
            "app": APP_NAME,
            "version": APP_VERSION,
            "asset_version": metadata.asset_version,
            "python_version": metadata.python_version,
            "packaging_mode": metadata.packaging_mode,
        }

    register_dashboard_routes(app, route_templates)
    register_hit_record_routes(app)
    register_sidebar_routes(app)
    register_target_routes(app, route_templates)
    register_settings_routes(app, route_templates)
    return app


__all__ = ["create_app"]
