"""Sidebar group monitoring action routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi import Request

from facebook_monitor.application.target_actions import pause_sidebar_group_monitoring_action
from facebook_monitor.application.target_actions import restart_sidebar_group_monitoring_action
from facebook_monitor.application.target_actions import TargetActionOutcome
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import run_web_db_operation
from facebook_monitor.webapp.dependencies import start_resident_scheduler_if_needed
from facebook_monitor.webapp.routes.sidebar_common import sidebar_bad_request

SidebarGroupAction = Callable[[Path, str], TargetActionOutcome]


def register_sidebar_group_action_routes(app: FastAPI) -> None:
    """註冊 sidebar group start / stop routes。"""

    @app.post("/api/sidebar/groups/{group_id}/start")
    async def start_sidebar_group_monitoring(
        request: Request,
        group_id: str,
    ) -> dict[str, object]:
        """開始 sidebar group 內 targets，route 只負責喚醒 scheduler 一次。"""

        return await _run_sidebar_group_action(
            request,
            group_id=group_id,
            action=restart_sidebar_group_monitoring_action,
            operation_name="sidebar.start_group_monitoring",
        )

    @app.post("/api/sidebar/groups/{group_id}/stop")
    async def stop_sidebar_group_monitoring(
        request: Request,
        group_id: str,
    ) -> dict[str, object]:
        """停止 sidebar group 內 targets，保留 target-scoped seen/history。"""

        return await _run_sidebar_group_action(
            request,
            group_id=group_id,
            action=pause_sidebar_group_monitoring_action,
            operation_name="sidebar.stop_group_monitoring",
        )


async def _run_sidebar_group_action(
    request: Request,
    *,
    group_id: str,
    action: SidebarGroupAction,
    operation_name: str,
) -> dict[str, object]:
    """執行 sidebar group action 並在成功後處理 scheduler side effect。"""

    try:
        db_path = get_db_path(request)
        outcome = await run_web_db_operation(
            lambda: action(db_path, group_id),
            operation_name=operation_name,
        )
        if outcome.start_scheduler:
            start_resident_scheduler_if_needed(request)
        elif outcome.wake_scheduler:
            get_scheduler_manager(request).wake()
    except ValueError as exc:
        raise sidebar_bad_request(exc) from exc
    return {
        "ok": outcome.ok,
        "updated_count": outcome.updated_count,
        "message": outcome.message,
    }
