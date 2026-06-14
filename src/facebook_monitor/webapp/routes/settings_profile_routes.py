"""Settings Facebook profile route registration。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import RedirectResponse

from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.webapp.dependencies import redirect_settings_with_error
from facebook_monitor.webapp.dependencies import redirect_settings_with_message
from facebook_monitor.webapp.profile_session import ProfileSessionError
from facebook_monitor.webapp.settings_use_cases import close_facebook_profile_for_settings
from facebook_monitor.webapp.settings_use_cases import open_facebook_profile_for_settings


def register_settings_profile_routes(app: FastAPI) -> None:
    """註冊 Facebook automation profile routes。"""

    @app.post("/settings/facebook/open")
    async def open_facebook_profile(request: Request) -> RedirectResponse:
        """開啟 Facebook automation profile 設定視窗。"""

        try:
            await open_facebook_profile_for_settings(request)
        except ProfileSessionError as exc:
            return redirect_settings_with_error(format_failure_message_text(str(exc)))
        return redirect_settings_with_message("Facebook 設定視窗已開啟")

    @app.post("/settings/facebook/close")
    async def close_facebook_profile(request: Request) -> RedirectResponse:
        """關閉 Facebook automation profile 設定視窗。"""

        await close_facebook_profile_for_settings(request)
        return redirect_settings_with_message("Facebook 設定視窗已關閉")


__all__ = ["register_settings_profile_routes"]
