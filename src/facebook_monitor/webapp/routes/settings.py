"""Settings routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_global_notification_settings
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import open_profile_options
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.profile_session import ProfileSessionError


def register_settings_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 settings / notification / profile routes。"""

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
            group_id="global-notification-test",
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
                    open_profile_options(request),
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
