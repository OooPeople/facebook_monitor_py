"""Dashboard page route registration。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.templating import Jinja2Templates

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.core.scan_limits import MIN_TARGET_POSTS
from facebook_monitor.core.scan_limits import MAX_TARGET_POSTS
from facebook_monitor.webapp.dependencies import get_db_path
from facebook_monitor.webapp.dependencies import get_scheduler_manager
from facebook_monitor.webapp.dependencies import get_session_started_at
from facebook_monitor.webapp.dependencies import load_app_theme
from facebook_monitor.webapp.dependencies import run_web_read_operation
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardViewModel


DashboardViewLoader = Callable[..., DashboardViewModel]
DashboardRevisionLoader = Callable[[Path], DashboardRevision]


def register_dashboard_page_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    *,
    get_dashboard_view: DashboardViewLoader,
    get_dashboard_revision: DashboardRevisionLoader,
) -> None:
    """註冊 dashboard HTML page route。"""

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
                "database_invariant_warning": dashboard.database_invariant_warning,
                "dashboard_revision": dashboard_revision,
                "target_defaults": PYTHON_TARGET_CONFIG_DEFAULTS,
                "min_refresh_seconds": MIN_REFRESH_SECONDS,
                "min_target_posts": MIN_TARGET_POSTS,
                "max_target_posts": MAX_TARGET_POSTS,
                "initial_theme": initial_theme,
            },
        )


__all__ = ["register_dashboard_page_routes"]
