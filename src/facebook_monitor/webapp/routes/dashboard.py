"""Dashboard routes。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import MIN_TARGET_POSTS
from facebook_monitor.core.scan_limits import MAX_TARGET_POSTS
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.dependencies import load_app_theme
from facebook_monitor.webapp.dependencies import run_web_read_operation
from facebook_monitor.webapp.dashboard_payloads import serialize_profile_session_warning
from facebook_monitor.webapp.dashboard_payloads import serialize_sidebar_item
from facebook_monitor.webapp.dashboard_payloads import serialize_sidebar_payload
from facebook_monitor.webapp.dashboard_payloads import serialize_target_card
from facebook_monitor.webapp.query_service import get_dashboard_revision
from facebook_monitor.webapp.query_service import get_dashboard_view
from facebook_monitor.webapp.query_service import get_target_card
from facebook_monitor.webapp.query_service import list_sidebar_items
from facebook_monitor.webapp.query_service import DashboardReadUnavailable
from facebook_monitor.webapp.query_service import DashboardRevision
from facebook_monitor.webapp.query_service import DashboardRevisionUnavailable


def _format_dashboard_revision_event(payload: dict[str, str]) -> str:
    """將 dashboard revision payload 包成 SSE dashboard_revision event。"""

    return (
        "event: dashboard_revision\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


async def _dashboard_revision_event_stream(
    request: Request,
    *,
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
            yield _format_dashboard_revision_event(
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


def register_dashboard_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 dashboard 與 revision API routes。"""

    @app.get("/")
    async def index(request: Request) -> object:
        """顯示 target 清單與設定表單。"""

        message = request.query_params.get("message", "")
        feedback = request.query_params.get("feedback", "")
        error = request.query_params.get("error", "")
        db_path = get_db_path(request)
        session_started_at = get_session_started_at(request)
        try:
            dashboard = await run_web_read_operation(
                lambda: get_dashboard_view(
                    db_path,
                    session_started_at=session_started_at,
                ),
                operation_name="dashboard.view",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        scheduler_state = get_scheduler_manager(request).state()
        try:
            dashboard_revision = await run_web_read_operation(
                lambda: get_dashboard_revision(db_path),
                operation_name="dashboard.revision_initial",
            )
        except DashboardRevisionUnavailable:
            dashboard_revision = DashboardRevision(revision="0", last_changed_at="")
        initial_theme = await load_app_theme(request)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "dashboard": dashboard,
                "rows": dashboard.rows,
                "message": message,
                "feedback": feedback,
                "error": error,
                "scheduler_state": scheduler_state,
                "profile_session_warning": dashboard.profile_session_warning,
                "dashboard_revision": dashboard_revision,
                "target_defaults": PYTHON_TARGET_CONFIG_DEFAULTS,
                "min_refresh_seconds": MIN_REFRESH_SECONDS,
                "min_target_posts": MIN_TARGET_POSTS,
                "max_target_posts": MAX_TARGET_POSTS,
                "initial_theme": initial_theme,
            },
        )

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
            _dashboard_revision_event_stream(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/sidebar")
    async def dashboard_sidebar(request: Request) -> dict[str, object]:
        """提供 Phase 10 partial update 使用的 sidebar read model。"""

        db_path = get_db_path(request)
        session_started_at = get_session_started_at(request)
        try:
            items = await run_web_read_operation(
                lambda: list_sidebar_items(
                    db_path,
                    session_started_at=session_started_at,
                ),
                operation_name="dashboard.sidebar",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {"items": [serialize_sidebar_item(item) for item in items]}

    @app.get("/api/dashboard-cards")
    async def dashboard_cards(request: Request) -> dict[str, object]:
        """提供 dashboard partial update 使用的 sidebar + card batch payload。"""

        db_path = get_db_path(request)
        session_started_at = get_session_started_at(request)
        try:
            dashboard = await run_web_read_operation(
                lambda: get_dashboard_view(
                    db_path,
                    session_started_at=session_started_at,
                ),
                operation_name="dashboard.cards",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {
            "profile_session_warning": serialize_profile_session_warning(
                dashboard.profile_session_warning
            ),
            "sidebar": serialize_sidebar_payload(dashboard),
            "cards": [serialize_target_card(row, templates) for row in dashboard.rows],
        }

    @app.get("/api/targets/{target_id}/card")
    async def target_card(request: Request, target_id: str) -> dict[str, object]:
        """提供 Phase 10 target-level partial update 的單卡 read model。"""

        db_path = get_db_path(request)
        session_started_at = get_session_started_at(request)
        try:
            row = await run_web_read_operation(
                lambda: get_target_card(
                    db_path,
                    target_id,
                    session_started_at=session_started_at,
                ),
                operation_name="dashboard.target_card",
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        if row is None:
            raise HTTPException(status_code=404, detail="target not found")
        return serialize_target_card(row, templates)
