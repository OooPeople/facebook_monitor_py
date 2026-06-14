"""Settings diagnostics route registration。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse

from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_notification_event_message
from facebook_monitor.notifications.safe_messages import safe_exception_message
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.settings_use_cases import (
    clear_failed_notifications_for_settings,
)
from facebook_monitor.webapp.settings_use_cases import create_support_bundle_for_settings


def register_settings_diagnostics_routes(app: FastAPI) -> None:
    """註冊 settings 診斷與清理 routes。"""

    @app.post("/settings/notifications/clear-failed")
    async def clear_failed_notification_outbox_route(request: Request) -> RedirectResponse:
        """手動清除 failed notification outbox rows，不影響 pending rows。"""

        try:
            cleared_count = await clear_failed_notifications_for_settings(request)
        except Exception as exc:
            return redirect_settings_with_error(
                "清除失敗通知失敗："
                + format_notification_event_message(
                    safe_exception_message("notification_clear_failed", exc)
                )
            )
        return redirect_settings_with_message(
            f"已清除失敗通知 {cleared_count} 筆",
            feedback="notification_clear_failed_finished",
        )

    @app.post("/settings/support-bundle")
    async def download_support_bundle(request: Request) -> object:
        """建立並下載 redacted support bundle。"""

        try:
            result = await create_support_bundle_for_settings(request)
        except Exception as exc:
            return redirect_settings_with_error(
                "支援診斷包建立失敗：" + format_failure_message_text(str(exc))
            )
        return FileResponse(
            result.path,
            media_type="application/zip",
            filename=result.filename,
        )


__all__ = ["register_settings_diagnostics_routes"]
