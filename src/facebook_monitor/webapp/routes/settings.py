"""Settings routes。"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.notifications.manual_test import send_manual_test_notification
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.persistence.repositories.app_settings import TargetKeywordDefaultSettings
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_app_theme
from facebook_monitor.webapp.dependencies import get_desktop_sender
from facebook_monitor.webapp.dependencies import get_discord_sender
from facebook_monitor.webapp.dependencies import get_global_notification_settings
from facebook_monitor.webapp.dependencies import get_ntfy_sender
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_profile_manager
from facebook_monitor.webapp.dependencies import get_target_keyword_defaults
from facebook_monitor.webapp.dependencies import open_profile_options
from facebook_monitor.webapp.dependencies import pause_scheduler_for_profile_use
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.dependencies import resume_scheduler_after_profile_use
from facebook_monitor.webapp.form_models import NotificationConfigForm
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.runtime_diagnostics import build_runtime_diagnostics_view


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
                "target_keyword_defaults": get_target_keyword_defaults(request),
                "runtime_diagnostics": build_runtime_diagnostics_view(request.app.state),
                "initial_theme": get_app_theme(request),
            },
        )

    @app.post("/settings/theme")
    async def update_theme(request: Request) -> dict[str, str]:
        """保存 Web UI theme preference，避免 auto-port 時遺失主題。"""

        payload = await request.json()
        theme = str(payload.get("theme", "")).strip()
        if theme not in {"light", "dark"}:
            raise HTTPException(status_code=400, detail="invalid theme")
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            saved_theme = app_context.repositories.app_settings.save_theme(theme)
        return {"theme": saved_theme}

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

        settings = NotificationConfigForm(
            enable_desktop_notification=enable_desktop_notification,
            enable_ntfy=enable_ntfy,
            ntfy_topic=ntfy_topic,
            enable_discord_notification=enable_discord_notification,
            discord_webhook=discord_webhook,
        ).to_global_settings()
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            app_context.repositories.global_notification_settings.save(settings)
        return redirect_settings_with_message("通知預設值已保存")

    @app.post("/settings/target-keywords")
    async def update_target_keyword_defaults(
        request: Request,
        exclude_keywords: Annotated[str, Form()] = "",
        exclude_ignore_phrases: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """更新新增 target 時套用的關鍵字預設值。"""

        settings = TargetKeywordDefaultSettings(
            exclude_keywords_text=exclude_keywords,
            exclude_ignore_phrases_text=exclude_ignore_phrases,
        )
        with SqliteApplicationContext(get_db_path(request)) as app_context:
            app_context.repositories.app_settings.save_target_keyword_defaults(settings)
        return redirect_settings_with_message("關鍵字預設值已保存")

    @app.post("/settings/notifications/apply-to-targets")
    async def apply_global_notifications_to_targets(request: Request) -> RedirectResponse:
        """將通知預設值套用到所有 target 設定。"""

        with SqliteApplicationContext(get_db_path(request)) as app_context:
            settings = app_context.repositories.global_notification_settings.get()
            count = app_context.services.targets.apply_global_notification_settings(settings)
        return redirect_settings_with_message(f"已套用通知預設值到 {count} 個 target 設定")

    @app.post("/settings/notifications/test")
    async def test_global_notifications(
        request: Request,
        enable_desktop_notification: Annotated[str | None, Form()] = None,
        enable_ntfy: Annotated[str | None, Form()] = None,
        ntfy_topic: Annotated[str, Form()] = "",
        enable_discord_notification: Annotated[str | None, Form()] = None,
        discord_webhook: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """依 settings 頁目前表單欄位送出一則測試通知，不保存設定。"""

        config = NotificationConfigForm(
            enable_desktop_notification=enable_desktop_notification,
            enable_ntfy=enable_ntfy,
            ntfy_topic=ntfy_topic,
            enable_discord_notification=enable_discord_notification,
            discord_webhook=discord_webhook,
        ).to_target_config(target_id="global-notification-test")
        try:
            results = await run_in_threadpool(
                send_manual_test_notification,
                config=config,
                ntfy_sender=get_ntfy_sender(request),
                desktop_sender=get_desktop_sender(request),
                discord_sender=get_discord_sender(request),
            )
        except Exception as exc:
            return redirect_settings_with_error(
                "測試通知失敗："
                + safe_exception_message("notification_test_failed", exc)
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
