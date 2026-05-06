"""FastAPI local management UI。

職責：提供本機瀏覽器操作介面，讓使用者管理 target 設定與觸發一次掃描。
核心資料操作仍委派 application service，掃描仍委派 worker 入口。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from dataclasses import replace
from collections.abc import Callable
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import CreateCommentsTargetRequest
from facebook_monitor.application.services import CreateGroupPostsTargetRequest
from facebook_monitor.application.services import DEFAULT_WEBUI_FIXED_REFRESH_SECONDS
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.facebook.route_detection import RouteDetectionError
from facebook_monitor.facebook.route_detection import clean_facebook_page_title
from facebook_monitor.facebook.route_detection import detect_group_comments_route
from facebook_monitor.facebook.route_detection import detect_group_posts_route
from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_name_with_profile
from facebook_monitor.notifications.desktop import send_desktop_notification
from facebook_monitor.notifications.discord import send_discord_notification
from facebook_monitor.notifications.dispatcher import DesktopSender
from facebook_monitor.notifications.dispatcher import DiscordSender
from facebook_monitor.notifications.dispatcher import NtfySender
from facebook_monitor.notifications.dispatcher import send_manual_test_notification
from facebook_monitor.notifications.ntfy import send_ntfy_notification
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.profile_session import ProfileSessionManager
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.query_service import get_dashboard_revision
from facebook_monitor.webapp.query_service import list_target_rows
from facebook_monitor.webapp.scheduler_session import AutoScanMode
from facebook_monitor.webapp.scheduler_session import BackgroundSchedulerManager
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = ROOT / "data" / "app.db"
DEFAULT_PROFILE_DIR = ROOT / "data" / "profiles" / "phase0_default"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def parse_keywords_text(text: str) -> tuple[str, ...]:
    """將表單 keyword 文字轉成去重 tuple。"""

    keywords: list[str] = []
    for raw_item in text.replace("\n", ",").split(","):
        keyword = raw_item.strip()
        if keyword:
            keywords.append(keyword)
    return tuple(dict.fromkeys(keywords))


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


def get_ntfy_sender(request: Request) -> NtfySender:
    """從 app state 取得測試通知用 ntfy sender。"""

    return getattr(request.app.state, "ntfy_sender")


def get_desktop_sender(request: Request) -> DesktopSender:
    """從 app state 取得測試通知用 desktop sender。"""

    return getattr(request.app.state, "desktop_sender")


def get_discord_sender(request: Request) -> DiscordSender:
    """從 app state 取得測試通知用 Discord sender。"""

    return getattr(request.app.state, "discord_sender")


def get_auto_scan_mode(request: Request) -> AutoScanMode:
    """從 app state 取得 Web UI 預設自動掃描模式。"""

    return getattr(request.app.state, "auto_scan_mode", AutoScanMode.RESIDENT)


def build_scheduler_options(request: Request) -> SchedulerSessionOptions:
    """依目前 app state 建立背景 scheduler 啟動設定。"""

    return SchedulerSessionOptions(
        db_path=get_db_path(request),
        profile_dir=get_profile_dir(request),
        auto_scan_mode=get_auto_scan_mode(request),
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
    auto_scan_mode: AutoScanMode = AutoScanMode.RESIDENT,
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
                    auto_scan_mode=app_instance.state.auto_scan_mode,
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
    app.state.auto_scan_mode = auto_scan_mode
    app.state.reset_targets_on_startup = reset_targets_on_startup
    app.state.reset_runtime_data_on_startup = reset_runtime_data_on_startup
    app.state.scheduler_paused_for_profile = False
    app.state.scheduler_resume_options = None
    app.state.ntfy_sender = ntfy_sender
    app.state.desktop_sender = desktop_sender
    app.state.discord_sender = discord_sender
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index(request: Request) -> object:
        """顯示 target 清單與設定表單。"""

        message = request.query_params.get("message", "")
        error = request.query_params.get("error", "")
        rows = list_target_rows(get_db_path(request))
        scheduler_state = get_scheduler_manager(request).state()
        queued_positions = {
            target_id: index + 1
            for index, target_id in enumerate(scheduler_state.queued_target_ids)
        }
        dashboard_revision = get_dashboard_revision(get_db_path(request))
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "rows": rows,
                "message": message,
                "error": error,
                "profile_active": get_profile_manager(request).is_active(),
                "scheduler_state": scheduler_state,
                "queued_positions": queued_positions,
                "dashboard_revision": dashboard_revision,
            },
        )

    @app.get("/api/dashboard-revision")
    async def dashboard_revision(request: Request) -> dict[str, str]:
        """提供首頁變更偵測用 revision，讓前端避免固定整頁刷新。"""

        revision = get_dashboard_revision(get_db_path(request))
        return {
            "revision": revision.revision,
            "last_changed_at": revision.last_changed_at,
        }

    @app.get("/targets/new")
    async def new_target(request: Request) -> object:
        """顯示新增 group posts target 表單。"""

        message = request.query_params.get("message", "")
        error = request.query_params.get("error", "")
        return templates.TemplateResponse(
            request,
            "new_target.html",
            {
                "message": message,
                "error": error,
                "notification_settings": get_global_notification_settings(request),
                "target_defaults": PYTHON_TARGET_CONFIG_DEFAULTS,
            },
        )

    @app.post("/targets")
    async def create_target(
        request: Request,
        group_url: Annotated[str, Form()],
        display_name: Annotated[str, Form()] = "",
        include_keywords: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str, Form()] = "",
        fixed_refresh_sec: Annotated[int, Form()] = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
        max_items_per_scan: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
        auto_load_more: Annotated[str | None, Form()] = None,
        auto_adjust_sort: Annotated[str | None, Form()] = None,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """從表單 URL 建立或更新 posts/comments target。"""

        try:
            custom_name = clean_facebook_page_title(display_name)
            route = detect_target_route_from_url(group_url.strip())
            if isinstance(route, DetectedCommentsTargetRoute):
                resolved_group_name = ""
                if not custom_name:
                    resolved_group_name = await run_with_temporary_profile_access(
                        request,
                        lambda: get_group_name_resolver(request)(
                            get_profile_dir(request),
                            route.group_canonical_url,
                        ),
                    )
                with SqliteApplicationContext(get_db_path(request)) as app_context:
                    app_context.services.targets.upsert_comments_target(
                        CreateCommentsTargetRequest(
                            group_id=route.group_id,
                            parent_post_id=route.parent_post_id,
                            canonical_url=route.canonical_url,
                            name=custom_name,
                            group_name=resolved_group_name,
                            include_keywords=parse_keywords_text(include_keywords),
                            exclude_keywords=parse_keywords_text(exclude_keywords),
                            fixed_refresh_sec=max(fixed_refresh_sec, 1),
                            max_items_per_scan=max_items_per_scan,
                            auto_load_more=auto_load_more == "on",
                            auto_adjust_sort=auto_adjust_sort == "on",
                            enable_desktop_notification=enable_desktop_notification == "on",
                            enable_ntfy=enable_ntfy == "on",
                            ntfy_topic=ntfy_topic.strip(),
                            enable_discord_notification=enable_discord_notification == "on",
                            discord_webhook=discord_webhook.strip(),
                        )
                    )
            else:
                resolved_group_name = ""
                if not custom_name:
                    resolved_group_name = await run_with_temporary_profile_access(
                        request,
                        lambda: get_group_name_resolver(request)(
                            get_profile_dir(request),
                            route.canonical_url,
                        ),
                    )
                with SqliteApplicationContext(get_db_path(request)) as app_context:
                    app_context.services.targets.upsert_group_posts_target(
                        CreateGroupPostsTargetRequest(
                            group_id=route.group_id,
                            canonical_url=route.canonical_url,
                            name=custom_name,
                            group_name=resolved_group_name,
                            include_keywords=parse_keywords_text(include_keywords),
                            exclude_keywords=parse_keywords_text(exclude_keywords),
                            fixed_refresh_sec=max(fixed_refresh_sec, 1),
                            max_items_per_scan=max_items_per_scan,
                            auto_load_more=auto_load_more == "on",
                            auto_adjust_sort=auto_adjust_sort == "on",
                            enable_desktop_notification=enable_desktop_notification == "on",
                            enable_ntfy=enable_ntfy == "on",
                            ntfy_topic=ntfy_topic.strip(),
                            enable_discord_notification=enable_discord_notification == "on",
                            discord_webhook=discord_webhook.strip(),
                        )
                    )
        except RouteDetectionError as exc:
            return redirect_new_target_with_error(str(exc))
        except GroupMetadataError as exc:
            return redirect_new_target_with_error(str(exc))
        except ProfileSessionError as exc:
            return redirect_new_target_with_error(str(exc))
        except Exception as exc:
            return redirect_new_target_with_error(f"新增失敗: {exc}")
        return redirect_with_message("target 已新增")

    @app.get("/settings")
    async def settings(request: Request) -> object:
        """顯示全域設定頁。"""

        message = request.query_params.get("message", "")
        error = request.query_params.get("error", "")
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "message": message,
                "error": error,
                "profile_dir": str(get_profile_dir(request)),
                "profile_active": get_profile_manager(request).is_active(),
                "notification_settings": get_global_notification_settings(request),
            },
        )

    @app.post("/settings/notifications")
    async def update_global_notifications(
        request: Request,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新 Web UI 通知預設值。"""

        settings = GlobalNotificationSettings(
            enable_desktop_notification=enable_desktop_notification == "on",
            enable_ntfy=enable_ntfy == "on",
            ntfy_topic=ntfy_topic.strip(),
            enable_discord_notification=enable_discord_notification == "on",
            discord_webhook=discord_webhook.strip(),
        )
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            app_context.repositories.global_notification_settings.save(settings)
        return redirect_settings_with_message("通知預設值已保存")

    @app.post("/settings/notifications/apply-to-targets")
    async def apply_global_notifications_to_targets(request: Request) -> RedirectResponse:
        """將通知預設值套用到所有社團設定。"""

        with SqliteApplicationContext(get_db_path(request)) as app_context:
            settings = app_context.repositories.global_notification_settings.get()
            count = app_context.services.targets.apply_global_notification_settings(settings)
        return redirect_settings_with_message(f"已套用通知預設值到 {count} 個社團設定")

    @app.post("/settings/notifications/test")
    async def test_global_notifications(request: Request) -> RedirectResponse:
        """依通知預設值送出一則測試通知。"""

        settings = get_global_notification_settings(request)
        config = TargetConfig(
            target_id="global-notification-test",
            enable_desktop_notification=settings.enable_desktop_notification,
            enable_ntfy=settings.enable_ntfy,
            ntfy_topic=settings.ntfy_topic,
            enable_discord_notification=settings.enable_discord_notification,
            discord_webhook=settings.discord_webhook,
        )
        results = await run_in_threadpool(
            send_manual_test_notification,
            config=config,
            ntfy_sender=get_ntfy_sender(request),
            desktop_sender=get_desktop_sender(request),
            discord_sender=get_discord_sender(request),
        )
        return redirect_settings_with_message("測試通知結果：" + " / ".join(results))

    @app.post("/settings/facebook/open")
    async def open_facebook_profile(request: Request) -> RedirectResponse:
        """開啟 Facebook automation profile 設定視窗。"""

        try:
            pause_scheduler_for_profile_use(request)
            try:
                await run_in_threadpool(
                    get_profile_manager(request).open,
                    ProfileSessionOptions(
                        profile_dir=get_profile_dir(request),
                    ),
                )
            except Exception:
                resume_scheduler_after_profile_use(request)
                raise
        except ProfileSessionError as exc:
            return redirect_settings_with_error(str(exc))
        return redirect_settings_with_message("Facebook 設定視窗已開啟")

    @app.post("/settings/facebook/close")
    async def close_facebook_profile(request: Request) -> RedirectResponse:
        """關閉 Facebook automation profile 設定視窗。"""

        await run_in_threadpool(get_profile_manager(request).close)
        resume_scheduler_after_profile_use(request)
        return redirect_settings_with_message("Facebook 設定視窗已關閉")

    @app.post("/targets/{target_id}/config")
    async def update_config(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
        include_keywords: Annotated[str, Form()] = "",
        exclude_keywords: Annotated[str, Form()] = "",
        fixed_refresh_sec: Annotated[int, Form()] = DEFAULT_WEBUI_FIXED_REFRESH_SECONDS,
        max_items_per_scan: Annotated[
            int,
            Form(),
        ] = PYTHON_TARGET_CONFIG_DEFAULTS.max_items_per_scan,
        auto_load_more: Annotated[str | None, Form()] = None,
        auto_adjust_sort: Annotated[str | None, Form()] = None,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新 target 所屬社團設定。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.update_target_config(
                    UpdateTargetConfigRequest(
                        target_id=target_id,
                        include_keywords=parse_keywords_text(include_keywords),
                        exclude_keywords=parse_keywords_text(exclude_keywords),
                        fixed_refresh_sec=max(fixed_refresh_sec, 1),
                        max_items_per_scan=max_items_per_scan,
                        auto_load_more=auto_load_more == "on",
                        auto_adjust_sort=auto_adjust_sort == "on",
                        enable_desktop_notification=enable_desktop_notification == "on",
                        enable_ntfy=enable_ntfy == "on",
                        ntfy_topic=ntfy_topic.strip(),
                        enable_discord_notification=enable_discord_notification == "on",
                        discord_webhook=discord_webhook.strip(),
                    )
                )
        except Exception as exc:
            return redirect_with_error(f"設定更新失敗: {exc}", return_to=return_to)
        return redirect_with_message("設定已更新", return_to=return_to)

    @app.post("/targets/{target_id}/start")
    async def start_target(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """重新開始單一 target，清 seen scope 並要求立即掃描。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.restart_target_monitoring(target_id)
            get_scheduler_manager(request).wake()
        except Exception as exc:
            return redirect_with_error(f"啟動失敗: {exc}", return_to=return_to)
        return redirect_with_message("target 已開始", return_to=return_to)

    @app.post("/scheduler/start")
    async def start_scheduler(
        request: Request,
        auto_scan_mode: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """啟動 Web UI 內建背景 scheduler。"""

        try:
            selected_mode = parse_auto_scan_mode(auto_scan_mode) or get_auto_scan_mode(request)
        except ValueError:
            return redirect_with_error("自動掃描模式不正確")
        request.app.state.auto_scan_mode = selected_mode
        get_scheduler_manager(request).start(
            replace(build_scheduler_options(request), auto_scan_mode=selected_mode)
        )
        return redirect_with_message("自動掃描已啟動")

    @app.post("/scheduler/stop")
    async def stop_scheduler(request: Request) -> RedirectResponse:
        """停止 Web UI 內建背景 scheduler。"""

        get_scheduler_manager(request).stop()
        return redirect_with_message("自動掃描已停止")

    @app.post("/targets/{target_id}/stop")
    async def stop_target(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """暫停單一 target，保留 seen scope 與歷史紀錄。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.pause_target_monitoring(target_id)
            get_scheduler_manager(request).wake()
        except Exception as exc:
            return redirect_with_error(f"停止失敗: {exc}", return_to=return_to)
        return redirect_with_message("target 已停止", return_to=return_to)

    @app.post("/targets/{target_id}/delete")
    async def delete_target(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """刪除單一 target。"""

        try:
            with SqliteApplicationContext(get_db_path(request)) as app_context:
                app_context.services.targets.delete_target(target_id)
        except Exception as exc:
            return redirect_with_error(f"刪除失敗: {exc}", return_to=return_to)
        return redirect_with_message("target 已刪除", return_to=return_to)

    @app.post("/targets/{target_id}/scan-once")
    async def scan_once(request: Request, target_id: str) -> RedirectResponse:
        """對單一 target 觸發一次掃描。"""

        # 延遲 import，避免測試 web UI route 時載入 Playwright worker。
        from facebook_monitor.worker.runner import WorkerOnceOptions
        from facebook_monitor.worker.runner import run_worker_once

        try:
            await run_in_threadpool(
                run_worker_once,
                WorkerOnceOptions(
                    profile_dir=get_profile_dir(request),
                    db_path=get_db_path(request),
                    target_id=target_id,
                ),
            )
        except Exception:
            return redirect_with_error("掃描失敗，請查看 terminal 或 scan_runs")
        return redirect_with_message("掃描完成")

    return app


@dataclass(frozen=True)
class DetectedCommentsTargetRoute:
    """保存 Web UI 由單篇貼文 URL 自動判斷出的 comments target route。"""

    group_id: str
    parent_post_id: str
    canonical_url: str

    @property
    def group_canonical_url(self) -> str:
        """回傳解析社團名稱時使用的社團首頁 URL。"""

        return f"https://www.facebook.com/groups/{self.group_id}"


@dataclass(frozen=True)
class DetectedPostsTargetRoute:
    """保存 Web UI 由社團首頁 URL 自動判斷出的 posts target route。"""

    group_id: str
    canonical_url: str


def detect_target_route_from_url(value: str) -> DetectedCommentsTargetRoute | DetectedPostsTargetRoute:
    """依 URL 自動判斷新增 target 類型，不要求使用者手動選 posts/comments。"""

    try:
        comments_route = detect_group_comments_route(value, page_title="")
    except RouteDetectionError:
        try:
            posts_route = detect_group_posts_route(value, page_title="")
        except RouteDetectionError as posts_error:
            raise posts_error
        return DetectedPostsTargetRoute(
            group_id=posts_route.group_id,
            canonical_url=posts_route.canonical_url,
        )
    return DetectedCommentsTargetRoute(
        group_id=comments_route.group_id,
        parent_post_id=comments_route.parent_post_id,
        canonical_url=comments_route.canonical_url,
    )


def redirect_with_message(message: str, *, return_to: str = "") -> RedirectResponse:
    """回到首頁並帶上成功訊息。"""

    return RedirectResponse(
        f"/?{urlencode({'message': message})}{normalize_return_fragment(return_to)}",
        status_code=303,
    )


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


def parse_auto_scan_mode(value: str) -> AutoScanMode | None:
    """解析 Web UI 自動掃描模式表單值。"""

    normalized = value.strip().replace("-", "_").lower()
    if not normalized:
        return None
    return AutoScanMode(normalized)


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


app = create_app(
    auto_start_scheduler=True,
    reset_targets_on_startup=True,
    reset_runtime_data_on_startup=True,
)
