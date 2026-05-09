"""Web UI shared dependencies。

職責：集中 FastAPI route 會共用的 app state accessors、redirect helper
與 profile/scheduler 協調 helper。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import utc_now
from facebook_monitor.facebook.group_metadata import resolve_group_name_with_profile
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.webapp.profile_session import ProfileSessionManager
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = ROOT / "data" / "app.db"
DEFAULT_PROFILE_DIR = ROOT / "data" / "profiles" / "automation_default"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_db_path(request: Request) -> Path:
    """從 app state 取得 SQLite DB path。"""

    return Path(getattr(request.app.state, "db_path", DEFAULT_DB_PATH))


def get_profile_dir(request: Request) -> Path:
    """從 app state 取得 Playwright profile path。"""

    return Path(getattr(request.app.state, "profile_dir", DEFAULT_PROFILE_DIR))


def get_profile_manager(request: Request) -> ProfileSessionManager:
    """從 app state 取得 automation profile session manager。"""

    return getattr(request.app.state, "profile_manager")


def get_group_name_resolver(request: Request) -> Callable[[Path, str], str]:
    """從 app state 取得 group name resolver。"""

    return getattr(request.app.state, "group_name_resolver")


def get_scheduler_manager(request: Request) -> BackgroundSchedulerManager:
    """從 app state 取得 Web UI 背景 scheduler manager。"""

    return getattr(request.app.state, "scheduler_manager")


def get_session_started_at(request: Request) -> datetime:
    """取得本次 Web UI session 起始時間，用於 session-scoped preview。"""

    value = getattr(request.app.state, "session_started_at", None)
    return value if isinstance(value, datetime) else utc_now()


def get_ntfy_sender(request: Request) -> NtfySender:
    """從 app state 取得測試通知用 ntfy sender。"""

    return getattr(request.app.state, "ntfy_sender")


def get_desktop_sender(request: Request) -> DesktopSender:
    """從 app state 取得測試通知用 desktop sender。"""

    return getattr(request.app.state, "desktop_sender")


def get_discord_sender(request: Request) -> DiscordSender:
    """從 app state 取得測試通知用 Discord sender。"""

    return getattr(request.app.state, "discord_sender")


def build_scheduler_options(request: Request) -> SchedulerSessionOptions:
    """依目前 app state 建立背景 scheduler 啟動設定。"""

    return SchedulerSessionOptions(
        db_path=get_db_path(request),
        profile_dir=get_profile_dir(request),
        interval_seconds=float(
            getattr(
                request.app.state,
                "scheduler_interval_seconds",
                DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
            )
        ),
        scheduler_tick_seconds=float(getattr(request.app.state, "scheduler_tick_seconds", 2)),
        max_concurrent_scans=int(getattr(request.app.state, "max_concurrent_scans", 2)),
    )


def pause_scheduler_for_profile_use(request: Request) -> None:
    """暫停內部 scheduler，讓 profile 設定或 metadata resolver 可獨占 profile。"""

    scheduler = get_scheduler_manager(request)
    if not scheduler.is_running():
        return
    request.app.state.scheduler_paused_for_profile = True
    request.app.state.scheduler_resume_options = scheduler.options or build_scheduler_options(request)
    scheduler.stop()


def resume_scheduler_after_profile_use(request: Request) -> None:
    """在 profile 使用結束後恢復內部 scheduler。"""

    if not getattr(request.app.state, "scheduler_paused_for_profile", False):
        return
    if get_profile_manager(request).is_active():
        return
    options = getattr(request.app.state, "scheduler_resume_options", None)
    if options is None:
        options = build_scheduler_options(request)
    get_scheduler_manager(request).start(options)
    request.app.state.scheduler_paused_for_profile = False
    request.app.state.scheduler_resume_options = None


async def run_with_temporary_profile_access(
    request: Request,
    action: Callable[[], str],
) -> str:
    """暫停 scheduler 執行短期 profile 工作，完成後立即恢復。"""

    was_running = get_scheduler_manager(request).is_running()
    pause_scheduler_for_profile_use(request)
    try:
        return await run_in_threadpool(action)
    finally:
        if was_running:
            request.app.state.scheduler_paused_for_profile = True
            resume_scheduler_after_profile_use(request)


def default_group_name_resolver(profile_dir: Path, canonical_url: str) -> str:
    """使用 automation profile 自動解析 Facebook group name。"""

    return resolve_group_name_with_profile(
        profile_dir=profile_dir,
        canonical_url=canonical_url,
    )


def get_global_notification_settings(request: Request) -> GlobalNotificationSettings:
    """讀取 Web UI 通知預設值。"""

    with SqliteApplicationContext(get_db_path(request)) as app_context:
        return app_context.repositories.global_notification_settings.get()


def redirect_with_message(message: str, *, return_to: str = "") -> RedirectResponse:
    """回到首頁並帶上成功訊息。"""

    return RedirectResponse(
        f"/?{urlencode({'message': message})}{normalize_return_fragment(return_to)}",
        status_code=303,
    )


def redirect_with_error(error: str, *, return_to: str = "") -> RedirectResponse:
    """回到首頁並帶上錯誤訊息。"""

    return RedirectResponse(
        f"/?{urlencode({'error': error})}{normalize_return_fragment(return_to)}",
        status_code=303,
    )


def normalize_return_fragment(value: str) -> str:
    """整理表單回傳 anchor，只允許 target card fragment。"""

    fragment = value.strip()
    if not fragment.startswith("#target-"):
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_#")
    if any(character not in allowed for character in fragment):
        return ""
    return fragment


def redirect_new_target_with_error(error: str) -> RedirectResponse:
    """回到新增頁並帶上錯誤訊息。"""

    return RedirectResponse(f"/targets/new?{urlencode({'error': error})}", status_code=303)


def redirect_new_target_with_message(message: str) -> RedirectResponse:
    """回到新增頁並帶上成功訊息。"""

    return RedirectResponse(f"/targets/new?{urlencode({'message': message})}", status_code=303)


def redirect_settings_with_message(message: str) -> RedirectResponse:
    """回到設定頁並帶上成功訊息。"""

    return RedirectResponse(f"/settings?{urlencode({'message': message})}", status_code=303)


def redirect_settings_with_error(error: str) -> RedirectResponse:
    """回到設定頁並帶上錯誤訊息。"""

    return RedirectResponse(f"/settings?{urlencode({'error': error})}", status_code=303)


def open_profile_options(request: Request) -> ProfileSessionOptions:
    """建立 profile session open options。"""

    return ProfileSessionOptions(profile_dir=get_profile_dir(request))


def start_resident_scheduler_if_needed(request: Request) -> None:
    """manual scan 需要 scheduler 時，以 resident mode 啟動或喚醒。"""

    scheduler = get_scheduler_manager(request)
    if not scheduler.is_running():
        scheduler.start(build_scheduler_options(request))
    scheduler.wake()
