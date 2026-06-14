"""Dashboard revision and SSE route registration。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse

from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import run_web_read_operation
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable


DashboardRevisionLoader = Callable[[Path], DashboardRevision]
DashboardRevisionFormatter = Callable[[dict[str, str]], str]


def format_dashboard_revision_event(payload: dict[str, str]) -> str:
    """將 dashboard revision payload 包成 SSE dashboard_revision event。"""

    return (
        "event: dashboard_revision\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def register_dashboard_revision_routes(
    app: FastAPI,
    *,
    get_dashboard_revision: DashboardRevisionLoader,
    format_revision_event: DashboardRevisionFormatter,
) -> None:
    """註冊 dashboard revision polling 與 SSE routes。"""

    @app.get("/api/dashboard-revision")
    async def dashboard_revision(request: Request) -> dict[str, str]:
        """提供首頁變更偵測用 revision，讓前端避免固定整頁刷新。"""

        db_path = get_db_path(request)
        try:
            revision = await run_web_read_operation(
                lambda: get_dashboard_revision(db_path),
                operation_name="dashboard.revision",
            )
        except DashboardRevisionUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard revision unavailable") from exc
        return {
            "revision": revision.revision,
            "last_changed_at": revision.last_changed_at,
        }

    @app.get("/api/dashboard-events")
    async def dashboard_events(request: Request) -> StreamingResponse:
        """提供 Phase 10A 使用的 dashboard revision SSE event stream。"""

        return StreamingResponse(
            dashboard_revision_event_stream(
                request,
                get_dashboard_revision=get_dashboard_revision,
                format_revision_event=format_revision_event,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


async def dashboard_revision_event_stream(
    request: Request,
    *,
    get_dashboard_revision: DashboardRevisionLoader,
    format_revision_event: DashboardRevisionFormatter,
    poll_interval_seconds: float = (
        PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_poll_interval_seconds
    ),
    keepalive_seconds: float = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_keepalive_seconds,
    max_connection_seconds: float = (
        PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_max_connection_seconds
    ),
) -> AsyncIterator[str]:
    """短生命週期輸出 dashboard revision SSE event，避免 shutdown 被長連線拖住。"""

    db_path = get_db_path(request)
    last_revision = ""
    keepalive_elapsed = 0.0
    connection_elapsed = 0.0
    while True:
        if await request.is_disconnected():
            break
        if connection_elapsed >= max_connection_seconds:
            break

        try:
            revision = await run_web_read_operation(
                lambda: get_dashboard_revision(db_path),
                operation_name="dashboard.revision_stream",
            )
        except DashboardRevisionUnavailable:
            await asyncio.sleep(poll_interval_seconds)
            keepalive_elapsed += poll_interval_seconds
            connection_elapsed += poll_interval_seconds
            continue
        if revision.revision != last_revision:
            last_revision = revision.revision
            keepalive_elapsed = 0.0
            yield format_revision_event(
                {
                    "revision": revision.revision,
                    "last_changed_at": revision.last_changed_at,
                }
            )
        elif keepalive_elapsed >= keepalive_seconds:
            keepalive_elapsed = 0.0
            yield ": keepalive\n\n"

        await asyncio.sleep(poll_interval_seconds)
        keepalive_elapsed += poll_interval_seconds
        connection_elapsed += poll_interval_seconds


__all__ = [
    "dashboard_revision_event_stream",
    "format_dashboard_revision_event",
    "register_dashboard_revision_routes",
]
