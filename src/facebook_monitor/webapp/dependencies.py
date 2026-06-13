"""Web UI shared dependencies。

職責：集中 FastAPI route 會共用的 app state accessors、redirect helper
與 profile/scheduler 協調 helper。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.redaction import redact_sensitive_text
from facebook_monitor.facebook.group_metadata import GroupMetadata
from facebook_monitor.facebook.group_metadata import resolve_group_metadata_with_profile
from facebook_monitor.notifications.senders import DesktopSender
from facebook_monitor.notifications.senders import DiscordSender
from facebook_monitor.notifications.senders import NtfySender
from facebook_monitor.persistence.repositories.app_settings import TargetKeywordDefaultSettings
from facebook_monitor.persistence.sqlite_retry import run_sqlite_operation_with_retry_async
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.webapp.profile_session import ProfileManagerLike
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.scheduler_session import SchedulerManagerLike
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.webapp.scheduler_session import SchedulerLifecycleState


DEFAULT_RUNTIME_PATHS = default_runtime_paths()
DEFAULT_DB_PATH = DEFAULT_RUNTIME_PATHS.db_path
DEFAULT_PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
TEMPLATES_DIR = DEFAULT_RUNTIME_PATHS.templates_dir
STATIC_DIR = DEFAULT_RUNTIME_PATHS.static_dir
logger = logging.getLogger(__name__)
DbOperationResult = TypeVar("DbOperationResult")
ProfileActionResult = TypeVar("ProfileActionResult")
GroupMetadataResolver = Callable[[Path, str], str | GroupMetadata]


def get_db_path(request: Request) -> Path:
    """從 app state 取得 SQLite DB path。"""

    return Path(getattr(request.app.state, "db_path", DEFAULT_DB_PATH))


def get_runtime_paths(request: Request) -> RuntimePaths:
    """從 app state 取得 runtime path resolver 結果。"""

    paths = getattr(request.app.state, "runtime_paths", None)
    return paths if isinstance(paths, RuntimePaths) else DEFAULT_RUNTIME_PATHS


def get_profile_dir(request: Request) -> Path:
    """從 app state 取得 Playwright profile path。"""

    return Path(getattr(request.app.state, "profile_dir", DEFAULT_PROFILE_DIR))


def get_profile_manager(request: Request) -> ProfileManagerLike:
    """從 app state 取得 automation profile session manager。"""

    return getattr(request.app.state, "profile_manager")


def get_group_name_resolver(request: Request) -> GroupMetadataResolver:
    """從 app state 取得 group name resolver。"""

    return getattr(request.app.state, "group_name_resolver")


def get_scheduler_manager(request: Request) -> SchedulerManagerLike:
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
                PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec,
            )
        ),
        scheduler_tick_seconds=float(
            getattr(
                request.app.state,
                "scheduler_tick_seconds",
                PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scheduler_tick_seconds,
            )
        ),
        max_concurrent_scans=int(
            getattr(
                request.app.state,
                "max_concurrent_scans",
                PYTHON_SCHEDULER_RUNTIME_DEFAULTS.max_concurrent_scans,
            )
        ),
    )


def pause_scheduler_for_profile_use(request: Request) -> None:
    """暫停內部 scheduler，讓 profile 設定或 metadata resolver 可獨占 profile。"""

    scheduler = get_scheduler_manager(request)
    if not scheduler.is_running():
        return
    request.app.state.scheduler_paused_for_profile = True
    request.app.state.scheduler_resume_options = scheduler.options or build_scheduler_options(request)
    scheduler.stop()
    if scheduler.state().lifecycle_state == SchedulerLifecycleState.STOPPING:
        return


def resume_scheduler_after_profile_use(request: Request) -> None:
    """在 profile 使用結束後恢復內部 scheduler。"""

    _resume_scheduler_after_profile_use_state(
        app_state=request.app.state,
        profile_manager=get_profile_manager(request),
        scheduler=get_scheduler_manager(request),
        fallback_options=build_scheduler_options(request),
    )


def _resume_scheduler_after_profile_use_state(
    *,
    app_state: object,
    profile_manager: ProfileManagerLike,
    scheduler: SchedulerManagerLike,
    fallback_options: SchedulerSessionOptions,
) -> None:
    """用 request-free state 恢復 scheduler，供 profile worker thread callback 使用。"""

    if not getattr(app_state, "scheduler_paused_for_profile", False):
        return
    if profile_manager.is_active():
        return
    options = getattr(app_state, "scheduler_resume_options", None)
    if options is None:
        options = fallback_options
    if scheduler.state().lifecycle_state == SchedulerLifecycleState.STOPPING:
        return
    try:
        scheduler.start(options)
    except RuntimeError as exc:
        if "stopping" in str(exc).lower():
            return
        raise
    setattr(app_state, "scheduler_paused_for_profile", False)
    setattr(app_state, "scheduler_resume_options", None)


async def run_with_temporary_profile_access(
    request: Request,
    action: Callable[[], ProfileActionResult],
) -> ProfileActionResult:
    """暫停 scheduler 執行短期 profile 工作，完成後立即恢復。"""

    was_running = get_scheduler_manager(request).is_running()
    pause_scheduler_for_profile_use(request)
    try:
        return await run_in_threadpool(action)
    finally:
        if was_running:
            request.app.state.scheduler_paused_for_profile = True
            resume_scheduler_after_profile_use(request)


async def run_web_db_operation(
    operation: Callable[[], DbOperationResult],
    *,
    operation_name: str,
) -> DbOperationResult:
    """在背景 thread 執行 Web route DB operation 並套用 SQLite lock retry。"""

    return await run_sqlite_operation_with_retry_async(
        operation,
        operation_name=f"web.{operation_name}",
        logger=logger,
    )


async def run_web_read_operation(
    operation: Callable[[], DbOperationResult],
    *,
    operation_name: str,
) -> DbOperationResult:
    """在背景 thread 執行 Web read-side operation，不改變既有 read 失敗語義。"""

    logger.debug("run web read operation off event loop", extra={"operation": operation_name})
    return await run_in_threadpool(operation)


async def run_web_app_context_operation(
    request: Request,
    operation: Callable[[ApplicationContext], DbOperationResult],
    *,
    operation_name: str,
) -> DbOperationResult:
    """以 Web route 共用 retry/thread 邊界執行 ApplicationContext operation。"""

    db_path = get_db_path(request)

    def run() -> DbOperationResult:
        """建立 thread-local SQLite context 後執行 route operation。"""

        with SqliteApplicationContext(db_path) as app_context:
            return operation(app_context)

    return await run_web_db_operation(run, operation_name=operation_name)


def default_group_name_resolver(profile_dir: Path, canonical_url: str) -> GroupMetadata:
    """使用 automation profile 自動解析 Facebook group metadata。"""

    return resolve_group_metadata_with_profile(
        profile_dir=profile_dir,
        canonical_url=canonical_url,
    )


def get_app_theme(request: Request) -> str:
    """讀取 Web UI DB-backed theme preference。"""

    return _get_app_theme_from_db(get_db_path(request))


async def load_app_theme(request: Request) -> str:
    """在 async route 中 off-thread 讀取 Web UI theme preference。"""

    db_path = get_db_path(request)
    return await run_web_read_operation(
        lambda: _get_app_theme_from_db(db_path),
        operation_name="settings.get_app_theme",
    )


def _get_app_theme_from_db(db_path: Path) -> str:
    """以指定 DB path 讀取 theme preference。"""

    with SqliteApplicationContext(db_path) as app_context:
        return app_context.repositories.app_settings.get_theme()


def get_target_keyword_defaults(request: Request) -> TargetKeywordDefaultSettings:
    """讀取新增 target 使用的關鍵字預設值。"""

    return _get_target_keyword_defaults_from_db(get_db_path(request))


async def load_target_keyword_defaults(request: Request) -> TargetKeywordDefaultSettings:
    """在 async route 中 off-thread 讀取新增 target 使用的關鍵字預設值。"""

    db_path = get_db_path(request)
    return await run_web_read_operation(
        lambda: _get_target_keyword_defaults_from_db(db_path),
        operation_name="settings.get_target_keyword_defaults",
    )


def _get_target_keyword_defaults_from_db(db_path: Path) -> TargetKeywordDefaultSettings:
    """以指定 DB path 讀取 target keyword defaults。"""

    with SqliteApplicationContext(db_path) as app_context:
        return app_context.repositories.app_settings.get_target_keyword_defaults()


def redirect_with_message(
    message: str,
    *,
    return_to: str = "",
    feedback: str = "",
) -> RedirectResponse:
    """回到首頁並帶上成功訊息。"""

    return RedirectResponse(
        f"/?{urlencode(build_feedback_query(message, feedback))}"
        f"{normalize_return_fragment(return_to)}",
        status_code=303,
    )


def redirect_with_error(error: str, *, return_to: str = "") -> RedirectResponse:
    """回到首頁並帶上錯誤訊息。"""

    return RedirectResponse(
        f"/?{urlencode({'error': redact_sensitive_text(error)})}"
        f"{normalize_return_fragment(return_to)}",
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

    return RedirectResponse(
        f"/targets/new?{urlencode({'error': redact_sensitive_text(error)})}",
        status_code=303,
    )


def redirect_new_target_with_message(message: str) -> RedirectResponse:
    """回到新增頁並帶上成功訊息。"""

    return RedirectResponse(f"/targets/new?{urlencode({'message': message})}", status_code=303)


def redirect_settings_with_message(message: str, *, feedback: str = "") -> RedirectResponse:
    """回到設定頁並帶上成功訊息。"""

    return RedirectResponse(
        f"/settings?{urlencode(build_feedback_query(message, feedback))}",
        status_code=303,
    )


def redirect_settings_with_error(error: str) -> RedirectResponse:
    """回到設定頁並帶上錯誤訊息。"""

    return RedirectResponse(
        f"/settings?{urlencode({'error': redact_sensitive_text(error)})}",
        status_code=303,
    )


def build_feedback_query(message: str, feedback: str = "") -> dict[str, str]:
    """建立 redirect 使用的穩定訊息與機器可讀 feedback code query。"""

    query = {"message": message}
    if feedback:
        query["feedback"] = feedback
    return query


def open_profile_options(request: Request) -> ProfileSessionOptions:
    """建立 profile session open options。"""

    profile_dir = get_profile_dir(request)
    profile_manager = get_profile_manager(request)
    scheduler = get_scheduler_manager(request)
    fallback_options = build_scheduler_options(request)
    app_state = request.app.state
    return ProfileSessionOptions(
        profile_dir=profile_dir,
        on_close=lambda: _resume_scheduler_after_profile_use_state(
            app_state=app_state,
            profile_manager=profile_manager,
            scheduler=scheduler,
            fallback_options=fallback_options,
        ),
    )


def start_resident_scheduler_if_needed(request: Request) -> None:
    """manual scan 需要 scheduler 時，以 resident mode 啟動或喚醒。"""

    scheduler = get_scheduler_manager(request)
    if get_profile_manager(request).is_active():
        request.app.state.scheduler_paused_for_profile = True
        request.app.state.scheduler_resume_options = (
            scheduler.options
            or getattr(request.app.state, "scheduler_resume_options", None)
            or build_scheduler_options(request)
        )
        if scheduler.is_running():
            scheduler.stop()
        return
    if not scheduler.is_running():
        scheduler.start(build_scheduler_options(request))
    scheduler.wake()
