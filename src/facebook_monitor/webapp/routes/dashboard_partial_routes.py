"""Dashboard partial update route registration。"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.templating import Jinja2Templates

from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.dependencies import run_web_read_operation
from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_payloads import serialize_profile_session_warning
from facebook_monitor.webapp.dashboard_payloads import serialize_database_invariant_warning
from facebook_monitor.webapp.dashboard_payloads import serialize_sidebar_item
from facebook_monitor.webapp.dashboard_payloads import serialize_sidebar_payload
from facebook_monitor.webapp.dashboard_payloads import serialize_target_card
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardViewModel


DashboardViewLoader = Callable[..., DashboardViewModel]
SidebarItemsLoader = Callable[..., tuple[SidebarTargetItem, ...]]
TargetCardLoader = Callable[..., TargetRow | None]


def register_dashboard_partial_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    *,
    get_dashboard_view: DashboardViewLoader,
    get_target_card: TargetCardLoader,
    list_sidebar_items: SidebarItemsLoader,
) -> None:
    """註冊 dashboard partial update API routes。"""

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
            "dashboard_degraded": dashboard.dashboard_degraded,
            "profile_session_warning": serialize_profile_session_warning(
                dashboard.profile_session_warning
            ),
            "database_invariant_warning": serialize_database_invariant_warning(
                dashboard.database_invariant_warning
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


__all__ = ["register_dashboard_partial_routes"]
