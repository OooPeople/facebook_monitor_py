"""Dashboard revision and SSE route registration。"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse

from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.webapp.dashboard_revision_notifier import DashboardRevisionNotifier
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_dashboard_revision_notifier
from facebook_monitor.webapp.dependencies import run_web_read_operation
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable


DashboardRevisionLoader = Callable[[Path], DashboardRevision]
DashboardRevisionFormatter = Callable[[dict[str, str]], str]


class DashboardSseRequest(Protocol):
    """SSE stream helper 需要的 request 介面。"""

    async def is_disconnected(self) -> bool:
        """回傳 client 是否已斷線。"""
        ...


def format_dashboard_revision_event(payload: dict[str, str]) -> str:
    """將 dashboard revision payload 包成 SSE dashboard_revision event。"""

    return (
        f"id: {payload.get('revision', '')}\n"
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
        """提供 dashboard 長 SSE revision event stream。"""

        return build_dashboard_revision_sse_response(
            request,
            notifier=get_dashboard_revision_notifier(request),
            format_revision_event=format_revision_event,
        )


def build_dashboard_revision_sse_response(
    request: DashboardSseRequest,
    *,
    notifier: DashboardRevisionNotifier,
    format_revision_event: DashboardRevisionFormatter,
    keepalive_seconds: float = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_keepalive_seconds,
    retry_milliseconds: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_retry_milliseconds,
) -> StreamingResponse:
    """建立 dashboard revision SSE response，集中 header 與 stream framing。"""

    return StreamingResponse(
        dashboard_revision_event_stream(
            request,
            notifier=notifier,
            format_revision_event=format_revision_event,
            keepalive_seconds=keepalive_seconds,
            retry_milliseconds=retry_milliseconds,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def dashboard_revision_event_stream(
    request: DashboardSseRequest,
    *,
    notifier: DashboardRevisionNotifier,
    format_revision_event: DashboardRevisionFormatter,
    keepalive_seconds: float = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_keepalive_seconds,
    retry_milliseconds: int = PYTHON_WEBUI_RUNTIME_DEFAULTS.sse_retry_milliseconds,
) -> AsyncGenerator[str, None]:
    """輸出 dashboard 長 SSE stream，revision 由 process-local notifier 提供。"""

    yield f"retry: {int(retry_milliseconds)}\n\n"
    revision_stream = notifier.subscribe()
    next_revision = asyncio.create_task(anext(revision_stream))
    try:
        while True:
            if await request.is_disconnected():
                break
            done, _pending = await asyncio.wait(
                {next_revision},
                timeout=max(0.05, float(keepalive_seconds)),
            )
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                revision = next_revision.result()
            except StopAsyncIteration:
                break
            yield format_revision_event(
                {
                    "revision": revision.revision,
                    "last_changed_at": revision.last_changed_at,
                }
            )
            next_revision = asyncio.create_task(anext(revision_stream))
    finally:
        next_revision.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await next_revision
        await revision_stream.aclose()


__all__ = [
    "build_dashboard_revision_sse_response",
    "dashboard_revision_event_stream",
    "format_dashboard_revision_event",
    "register_dashboard_revision_routes",
]
