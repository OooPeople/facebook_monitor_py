"""Facebook access circuit Web action routes。"""

from __future__ import annotations

from typing import Annotated
import logging

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse

from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_profile_dir
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_app_context_operation
from facebook_monitor.webapp.facebook_access_recovery import (
    request_facebook_access_recovery_check,
)
from facebook_monitor.webapp.facebook_access_recovery import (
    request_facebook_session_recovery_check,
)
from facebook_monitor.webapp.facebook_access_runtime_hold import (
    read_facebook_automation_runtime_hold,
)


logger = logging.getLogger(__name__)


def register_facebook_access_routes(app: FastAPI) -> None:
    """註冊只會登記 probe request 的 manual recovery route。"""

    @app.post("/facebook-access/recovery-check")
    async def request_recovery_check(
        request: Request,
        candidate: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """不建立 browser 也不 claim；寫入 request 後只喚醒 scheduler。"""

        profile_dir = get_profile_dir(request)
        scheduler = get_scheduler_manager(request)
        try:
            scheduler_state = scheduler.state()
            runtime_hold = read_facebook_automation_runtime_hold(
                db_path=get_db_path(request),
                profile_dir=profile_dir,
                browser_session_active=bool(
                    scheduler_state.resident_browser_alive
                ),
            )
            if runtime_hold == "unclean_session_hold":
                outcome = await run_web_app_context_operation(
                    request,
                    lambda app_context: request_facebook_session_recovery_check(
                        app_context,
                        profile_dir=profile_dir,
                        requested_candidate=candidate,
                    ),
                    operation_name="facebook_access.request_session_recovery_check",
                )
            elif runtime_hold:
                return redirect_with_error(
                    "Facebook 自動化安全儲存狀態異常，不能執行恢復檢查。"
                )
            else:
                outcome = await run_web_app_context_operation(
                    request,
                    lambda app_context: request_facebook_access_recovery_check(
                        app_context,
                        profile_dir=profile_dir,
                        requested_candidate=candidate,
                    ),
                    operation_name="facebook_access.request_recovery_check",
                )
        except Exception:
            logger.exception("facebook access recovery request failed")
            return redirect_with_error("恢復檢查要求失敗，請稍後再試。")
        if not outcome.ok:
            return redirect_with_error(outcome.message)
        if outcome.wake_scheduler:
            scheduler.wake()
        return redirect_with_message(
            outcome.message,
            feedback=outcome.feedback,
        )


__all__ = ["register_facebook_access_routes"]
