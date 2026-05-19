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

from facebook_monitor.core.defaults import PYTHON_WEBUI_RUNTIME_DEFAULTS
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_app_theme
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.query_service import ProfileSessionWarning
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow
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


def _serialize_sidebar_item(item: SidebarTargetItem) -> dict[str, object]:
    """序列化 sidebar item read model。"""

    return {
        "target_id": item.target_id,
        "display_name": item.display_name,
        "anchor_id": item.anchor_id,
        "base_status_summary": item.base_status_summary,
        "status_class": item.status_class,
        "status_detail": item.status_detail,
        "status_summary": item.status_summary,
        "mode_label": item.mode_label,
        "mode_class": item.mode_class,
        "hit_count": item.hit_count,
        "latest_error_summary": item.latest_error_summary,
        "thumbnail_url": item.thumbnail_url,
    }


def _serialize_profile_session_warning(
    warning: ProfileSessionWarning,
) -> dict[str, object]:
    """序列化首頁 Facebook session 警告。"""

    return {
        "needs_login": warning.needs_login,
        "message": warning.message,
        "reason": warning.reason,
    }


def _render_collapsed_summary_html(templates: Jinja2Templates, row: TargetRow) -> str:
    """以 Jinja 單一來源產生收合摘要 partial HTML。"""

    template = templates.env.get_template("_collapsed_summary_fields.html")
    return template.render(summary_sections=row.card_summary.sections).strip()


def _render_preview_rows_html(
    templates: Jinja2Templates,
    rows: object,
    empty_text: str,
) -> str:
    """以 Jinja 單一來源產生 preview rows partial HTML。"""

    template = templates.env.get_template("_preview_rows.html")
    preview_rows = getattr(template.module, "preview_rows")
    return str(preview_rows(rows, empty_text)).strip()


def _serialize_target_card(row: TargetRow, templates: Jinja2Templates) -> dict[str, object]:
    """序列化 target card partial update read model。"""

    return {
        "target_id": row.target_id,
        "anchor_id": row.anchor_id,
        "display_name": row.display_name,
        "rename_display_name": row.rename_display_name,
        "thumbnail_url": row.thumbnail_url,
        "status_label": row.status_label,
        "status_class": row.status_class,
        "header_summary_label": row.header_summary_label,
        "mode_label": row.mode_label,
        "mode_class": row.mode_class,
        "monitoring_action": row.monitoring_action,
        "monitoring_button_label": row.monitoring_button_label,
        "runtime_error": row.runtime_error,
        "runtime_skip_reason": row.runtime_skip_reason,
        "has_latest_failed_scan": bool(row.latest_failed_scan_run),
        "latest_error_indicator_label": row.latest_error_indicator_label,
        "latest_error_indicator_title": row.latest_error_indicator_title,
        "latest_error_indicator_kind": row.latest_error_indicator_kind,
        "latest_scan_header_label": f"最近掃描 {row.latest_scan_header_time_label}",
        "next_refresh_label": f"下次刷新：{row.next_refresh_label}",
        "next_refresh_seconds": row.next_refresh_seconds,
        "scan_cycle_result_label": row.scan_cycle_result_label,
        "latest_scan_diagnostics_summary": row.latest_scan_diagnostics_summary,
        "latest_scan_diagnostics_text": row.latest_scan_diagnostics_text,
        "hit_record_total_count": row.hit_record_total_count,
        "card_summary_html": _render_collapsed_summary_html(templates, row),
        "latest_scan_preview_html": _render_preview_rows_html(
            templates,
            row.latest_scan_preview_rows,
            "尚無掃描紀錄",
        ),
        "hit_record_preview_html": _render_preview_rows_html(
            templates,
            row.hit_record_preview_rows,
            "尚無命中紀錄",
        ),
    }


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

    last_revision = ""
    keepalive_elapsed = 0.0
    connection_elapsed = 0.0
    while True:
        if await request.is_disconnected():
            break
        if connection_elapsed >= max_connection_seconds:
            break

        try:
            revision = get_dashboard_revision(get_db_path(request))
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
        error = request.query_params.get("error", "")
        try:
            dashboard = get_dashboard_view(
                get_db_path(request),
                session_started_at=get_session_started_at(request),
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        scheduler_state = get_scheduler_manager(request).state()
        try:
            dashboard_revision = get_dashboard_revision(get_db_path(request))
        except DashboardRevisionUnavailable:
            dashboard_revision = DashboardRevision(revision="0", last_changed_at="")
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "dashboard": dashboard,
                "rows": dashboard.rows,
                "message": message,
                "error": error,
                "scheduler_state": scheduler_state,
                "profile_session_warning": dashboard.profile_session_warning,
                "dashboard_revision": dashboard_revision,
                "initial_theme": get_app_theme(request),
            },
        )

    @app.get("/api/dashboard-revision")
    async def dashboard_revision(request: Request) -> dict[str, str]:
        """提供首頁變更偵測用 revision，讓前端避免固定整頁刷新。"""

        try:
            revision = get_dashboard_revision(get_db_path(request))
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

        try:
            items = list_sidebar_items(
                get_db_path(request),
                session_started_at=get_session_started_at(request),
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {"items": [_serialize_sidebar_item(item) for item in items]}

    @app.get("/api/dashboard-cards")
    async def dashboard_cards(request: Request) -> dict[str, object]:
        """提供 dashboard partial update 使用的 sidebar + card batch payload。"""

        try:
            dashboard = get_dashboard_view(
                get_db_path(request),
                session_started_at=get_session_started_at(request),
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        return {
            "profile_session_warning": _serialize_profile_session_warning(
                dashboard.profile_session_warning
            ),
            "sidebar": {
                "items": [_serialize_sidebar_item(row.sidebar_item) for row in dashboard.rows],
            },
            "cards": [_serialize_target_card(row, templates) for row in dashboard.rows],
        }

    @app.get("/api/targets/{target_id}/card")
    async def target_card(request: Request, target_id: str) -> dict[str, object]:
        """提供 Phase 10 target-level partial update 的單卡 read model。"""

        try:
            row = get_target_card(
                get_db_path(request),
                target_id,
                session_started_at=get_session_started_at(request),
            )
        except DashboardReadUnavailable as exc:
            raise HTTPException(status_code=503, detail="dashboard data unavailable") from exc
        if row is None:
            raise HTTPException(status_code=404, detail="target not found")
        return _serialize_target_card(row, templates)
