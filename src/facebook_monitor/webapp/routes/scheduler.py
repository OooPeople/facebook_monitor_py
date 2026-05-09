"""Scheduler routes。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import RedirectResponse

from facebook_monitor.webapp.dependencies import build_scheduler_options
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import redirect_with_message


def register_scheduler_routes(app: FastAPI) -> None:
    """註冊 Web UI 內部 scheduler routes。"""

    @app.post("/scheduler/start")
    async def start_scheduler(request: Request) -> RedirectResponse:
        """啟動 Web UI 內建 resident scheduler。"""

        get_scheduler_manager(request).start(build_scheduler_options(request))
        return redirect_with_message("自動掃描已啟動")

    @app.post("/scheduler/stop")
    async def stop_scheduler(request: Request) -> RedirectResponse:
        """停止 Web UI 內建背景 scheduler。"""

        get_scheduler_manager(request).stop()
        return redirect_with_message("自動掃描已停止")
