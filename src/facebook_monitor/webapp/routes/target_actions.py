"""Target action routes。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.responses import RedirectResponse

from facebook_monitor.application.target_actions import delete_target_action
from facebook_monitor.application.target_actions import pause_target_monitoring_action
from facebook_monitor.application.target_actions import request_target_scan_once_action
from facebook_monitor.application.target_actions import restart_target_monitoring_action
from facebook_monitor.application.target_actions import reset_target_notification_state_action
from facebook_monitor.application.target_actions import TargetActionOutcome
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import redirect_with_error
from facebook_monitor.webapp.dependencies import redirect_with_message
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed


logger = logging.getLogger(__name__)


async def _run_target_action_redirect(
    request: Request,
    *,
    target_id: str,
    return_to: str,
    action: Callable[[Path, str], TargetActionOutcome],
    failure_prefix: str,
    log_exception_message: str = "",
) -> RedirectResponse:
    """執行 target action 並集中 redirect / scheduler side effect 語義。"""

    try:
        db_path = get_db_path(request)
        outcome = await run_web_db_operation(
            lambda: action(db_path, target_id),
            operation_name=f"target_action.{action.__name__}",
        )
        if not outcome.ok:
            return redirect_with_error(outcome.message, return_to=return_to)
        if outcome.wake_scheduler:
            get_scheduler_manager(request).wake()
        if outcome.start_scheduler:
            start_resident_scheduler_if_needed(request)
    except Exception as exc:
        if log_exception_message:
            logger.exception(log_exception_message, extra={"target_id": target_id})
        return redirect_with_error(
            failure_prefix + format_failure_message_text(str(exc)),
            return_to=return_to,
        )
    return redirect_with_message(
        outcome.message,
        return_to=return_to,
        feedback=outcome.feedback,
    )


def register_target_action_routes(app: FastAPI) -> None:
    """註冊 target start/stop/delete/scan routes。"""

    @app.post("/targets/{target_id}/start")
    async def restart_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """開始單一 target，保留 seen/outbox 並要求立即掃描。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=restart_target_monitoring_action,
            failure_prefix="啟動失敗：",
        )

    @app.post("/targets/{target_id}/notifications/clear")
    async def reset_target_notification_state_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """重置單一 target 的通知與 seen 去重狀態。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=reset_target_notification_state_action,
            failure_prefix="重置通知狀態失敗：",
        )

    @app.post("/targets/{target_id}/stop")
    async def pause_target_monitoring_route(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """暫停單一 target，保留 seen scope 與歷史紀錄。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=pause_target_monitoring_action,
            failure_prefix="停止失敗：",
        )

    @app.post("/targets/{target_id}/delete")
    async def delete_target(
        request: Request,
        target_id: str,
        return_to: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        """刪除單一 target。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to=return_to,
            action=delete_target_action,
            failure_prefix="刪除失敗：",
        )

    @app.post("/targets/{target_id}/scan-once")
    async def scan_once(request: Request, target_id: str) -> RedirectResponse:
        """要求 resident scheduler 對單一 target 執行一次掃描。"""

        return await _run_target_action_redirect(
            request,
            target_id=target_id,
            return_to="",
            action=request_target_scan_once_action,
            failure_prefix="掃描失敗：",
            log_exception_message="scan once failed",
        )
