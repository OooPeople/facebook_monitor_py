"""FastAPI local management UI assembly。

職責：建立 FastAPI app、掛載 static/templates、管理 lifespan 與註冊 route modules。
實際 route handler 分散於 `webapp.routes.*`。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest
from secrets import token_urlsafe
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response
from starlette.types import Scope

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.maintenance import run_bounded_retention_maintenance_for_db
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core import input_limits
from facebook_monitor.core.models import utc_now
from facebook_monitor.notifications.channel_dispatch import DesktopSender
from facebook_monitor.notifications.channel_dispatch import DiscordSender
from facebook_monitor.notifications.channel_dispatch import NtfySender
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.runtime.build_metadata import collect_build_metadata
from facebook_monitor.version import APP_NAME
from facebook_monitor.version import APP_VERSION
from facebook_monitor.webapp.assets import ASSET_VERSION
from facebook_monitor.webapp.dependencies import DEFAULT_DB_PATH
from facebook_monitor.webapp.dependencies import DEFAULT_PROFILE_DIR
from facebook_monitor.webapp.dependencies import STATIC_DIR
from facebook_monitor.webapp.dependencies import TEMPLATES_DIR
from facebook_monitor.webapp.dependencies import build_scheduler_options
from facebook_monitor.webapp.dependencies import default_group_name_resolver
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_app_theme
from facebook_monitor.webapp.dependencies import get_group_name_resolver
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.dependencies import GroupMetadataResolver
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_new_target_with_error
from facebook_monitor.webapp.dependencies import redirect_new_target_with_message
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.dependencies import run_with_temporary_profile_access
from facebook_monitor.core.keyword_text import parse_keywords_text
from facebook_monitor.webapp.profile_session import ProfileManagerLike
from facebook_monitor.webapp.profile_session import ProfileSessionManager
from facebook_monitor.webapp.routes.dashboard import register_dashboard_routes
from facebook_monitor.webapp.routes.hit_records import register_hit_record_routes
from facebook_monitor.webapp.routes.settings import register_settings_routes
from facebook_monitor.webapp.routes.sidebar import register_sidebar_routes
from facebook_monitor.webapp.routes.targets import register_target_routes
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerManagerLike
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions


templates = _default_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_default_templates.env.globals["asset_version"] = ASSET_VERSION
_default_templates.env.globals["csrf_token"] = ""
_default_templates.env.globals["input_limits"] = input_limits


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
LOCAL_UI_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: https://fbcdn.net https://*.fbcdn.net "
        "https://fbsbx.com https://*.fbsbx.com https://facebook.com https://*.facebook.com",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    )
)


class RequestBodyTooLarge(Exception):
    """HTTP request body 超過本機管理 UI 可接受上限。"""


class LocalStaticFiles(StaticFiles):
    """本機 Web UI 靜態檔，每次瀏覽器重整都應重新驗證。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        """回傳 static response，避免 ES module 長時間沿用舊版。"""

        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


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
    route_templates = _build_templates(templates_dir, csrf_token=csrf_token_value)

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        """管理 Web UI 啟動與關閉時的背景 scheduler 生命週期。"""

        with SqliteApplicationContext(app_instance.state.db_path) as app_context:
            app_context.repositories.match_history.prune_global_limit()
        run_bounded_retention_maintenance_for_db(app_instance.state.db_path)
        if app_instance.state.reset_runtime_data_on_startup:
            with SqliteApplicationContext(app_instance.state.db_path) as app_context:
                app_context.repositories.maintenance.clear_startup_runtime_data()
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
            try:
                app_instance.state.profile_manager.close()
            finally:
                app_instance.state.scheduler_manager.stop()

    app = FastAPI(title="Facebook Monitor Local UI", lifespan=lifespan)

    @app.middleware("http")
    async def bounded_retention_maintenance_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """在低頻 read path 嘗試 housekeeping，避免完全依賴 scheduler tick。"""

        if request.method == "GET" and request.url.path in {"/", "/settings", "/health"}:
            run_bounded_retention_maintenance_for_db(request.app.state.db_path)
        return await call_next(request)

    app.state.db_path = db_path
    app.state.profile_dir = profile_dir
    app.state.templates_dir = templates_dir
    app.state.static_dir = static_dir
    app.state.profile_manager = profile_manager or ProfileSessionManager()
    app.state.group_name_resolver = group_name_resolver or default_group_name_resolver
    app.state.scheduler_manager = scheduler_manager or BackgroundSchedulerManager()
    app.state.auto_start_scheduler = auto_start_scheduler
    app.state.scheduler_interval_seconds = scheduler_interval_seconds
    app.state.scheduler_tick_seconds = scheduler_tick_seconds
    app.state.max_concurrent_scans = max_concurrent_scans
    app.state.session_started_at = utc_now()
    app.state.reset_targets_on_startup = reset_targets_on_startup
    app.state.resume_active_targets_on_startup = False
    app.state.reset_runtime_data_on_startup = reset_runtime_data_on_startup
    app.state.scheduler_paused_for_profile = False
    app.state.scheduler_resume_options = None
    app.state.ntfy_sender = ntfy_sender
    app.state.desktop_sender = desktop_sender
    app.state.discord_sender = discord_sender
    app.state.csrf_token = csrf_token_value
    app.state.enforce_csrf = enforce_csrf
    app.state.max_request_body_bytes = max(1, int(max_request_body_bytes))
    app.mount("/static", LocalStaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def csrf_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """保護本機管理 UI 的 mutating routes，避免跨站表單直接操作 localhost。"""

        try:
            request_body = await _read_request_body_with_limit(
                request,
                max_bytes=int(getattr(request.app.state, "max_request_body_bytes")),
            )
            if request_body is not None:
                request = _replay_request_body(request, request_body)
            if _should_validate_csrf(request):
                submitted_token = request.headers.get(CSRF_HEADER, "").strip()
                if not submitted_token:
                    submitted_token = _submitted_csrf_token_from_body(
                        request,
                        request_body or b"",
                    )
                expected_token = str(getattr(request.app.state, "csrf_token", ""))
                if not submitted_token or not compare_digest(submitted_token, expected_token):
                    return _with_security_headers(
                        Response("CSRF validation failed", status_code=403)
                    )
            response = await call_next(request)
        except RequestBodyTooLarge:
            return _with_security_headers(
                Response("Request body too large", status_code=413)
            )
        return _with_security_headers(response)

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


def _build_templates(templates_dir: Path, *, csrf_token: str = "") -> Jinja2Templates:
    """建立 Jinja template environment，讓 launcher 可傳入已解析 resource path。"""

    template_environment = Jinja2Templates(directory=str(templates_dir))
    template_environment.env.globals["asset_version"] = ASSET_VERSION
    template_environment.env.globals["csrf_token"] = csrf_token
    template_environment.env.globals["input_limits"] = input_limits
    return template_environment


def _should_validate_csrf(request: Request) -> bool:
    """判斷目前 request 是否需要 CSRF token。"""

    if request.method.upper() not in UNSAFE_METHODS:
        return False
    if not bool(getattr(request.app.state, "enforce_csrf", True)):
        return False
    return True


def _with_security_headers(response: Response) -> Response:
    """加上本機 Web UI 的基本安全 header。"""

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        LOCAL_UI_CONTENT_SECURITY_POLICY,
    )
    return response


def _submitted_csrf_token_from_body(request: Request, body: bytes) -> str:
    """從已讀取的 urlencoded body 解析 CSRF token。"""

    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        decoded_body = body.decode("utf-8", errors="replace")
        values = parse_qs(decoded_body).get(CSRF_FORM_FIELD, [])
        return str(values[0]).strip() if values else ""
    return ""


async def _read_request_body_with_limit(
    request: Request,
    *,
    max_bytes: int,
) -> bytes | None:
    """在進入 route 前讀取並限制 request body；無 body 時回傳 None。"""

    limit = max(1, int(max_bytes))
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > limit:
            raise RequestBodyTooLarge
    if not _request_may_have_body(request, content_length=content_length):
        return None
    chunks: list[bytes] = []
    received_bytes = 0
    while True:
        message = await request.receive()
        if message.get("type") == "http.request":
            body = message.get("body", b"")
            if isinstance(body, bytes):
                chunks.append(body)
                received_bytes += len(body)
            if received_bytes > limit:
                raise RequestBodyTooLarge
            if not bool(message.get("more_body", False)):
                break
        elif message.get("type") == "http.disconnect":
            break
    body = b"".join(chunks)
    setattr(request, "_body", body)
    return body


def _request_may_have_body(request: Request, *, content_length: str) -> bool:
    """判斷是否需要預先讀 body 才能套用大小限制與 replay。"""

    if content_length and content_length != "0":
        return True
    return request.method.upper() not in {"GET", "HEAD", "OPTIONS", "TRACE"}


def _replay_request_body(request: Request, body: bytes) -> Request:
    """重建 request receive，避免 middleware 讀 body 後 route 讀不到 form。"""

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(request.scope, receive)


__all__ = [
    "ASSET_VERSION",
    "DEFAULT_DB_PATH",
    "DEFAULT_PROFILE_DIR",
    "build_scheduler_options",
    "create_app",
    "get_db_path",
    "get_desktop_sender",
    "get_discord_sender",
    "get_app_theme",
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
