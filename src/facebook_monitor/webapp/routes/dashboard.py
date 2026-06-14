"""Dashboard route aggregator。

職責：保留既有 dashboard route registration 與測試 monkeypatch seam，實際
handler 分散於 dashboard_page / dashboard_revision_routes / dashboard_partial_routes。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.dashboard_queries import get_dashboard_view
from facebook_monitor.webapp.dashboard_queries import get_target_card
from facebook_monitor.webapp.dashboard_queries import list_sidebar_items
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardViewModel
from facebook_monitor.webapp.dashboard_revision_query import get_dashboard_revision
from facebook_monitor.webapp.routes.dashboard_page import register_dashboard_page_routes
from facebook_monitor.webapp.routes.dashboard_partial_routes import (
    register_dashboard_partial_routes,
)
from facebook_monitor.webapp.routes.dashboard_revision_routes import (
    format_dashboard_revision_event as _format_dashboard_revision_event,
)
from facebook_monitor.webapp.routes.dashboard_revision_routes import (
    register_dashboard_revision_routes,
)


def register_dashboard_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 dashboard HTML、revision 與 partial update API routes。"""

    register_dashboard_page_routes(
        app,
        templates,
        get_dashboard_view=_load_dashboard_view,
        get_dashboard_revision=_load_dashboard_revision,
    )
    register_dashboard_revision_routes(
        app,
        get_dashboard_revision=_load_dashboard_revision,
        format_revision_event=_format_revision_event,
    )
    register_dashboard_partial_routes(
        app,
        templates,
        get_dashboard_view=_load_dashboard_view,
        get_target_card=_load_target_card,
        list_sidebar_items=_load_sidebar_items,
    )


def _load_dashboard_view(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> DashboardViewModel:
    """透過 module global 載入 dashboard view，保留 dashboard monkeypatch seam。"""

    return get_dashboard_view(db_path, session_started_at=session_started_at)


def _load_dashboard_revision(db_path: Path) -> DashboardRevision:
    """透過 module global 載入 revision，保留 dashboard monkeypatch seam。"""

    return get_dashboard_revision(db_path)


def _load_sidebar_items(
    db_path: Path,
    *,
    session_started_at: datetime | None = None,
) -> tuple[SidebarTargetItem, ...]:
    """透過 module global 載入 sidebar items，保留 dashboard monkeypatch seam。"""

    return list_sidebar_items(db_path, session_started_at=session_started_at)


def _load_target_card(
    db_path: Path,
    target_id: str,
    *,
    session_started_at: datetime | None = None,
) -> TargetRow | None:
    """透過 module global 載入 target card，保留 dashboard monkeypatch seam。"""

    return get_target_card(
        db_path,
        target_id,
        session_started_at=session_started_at,
    )


def _format_revision_event(payload: dict[str, str]) -> str:
    """透過 module global 格式化 SSE event，保留 dashboard monkeypatch seam。"""

    return _format_dashboard_revision_event(payload)


__all__ = [
    "_format_dashboard_revision_event",
    "get_dashboard_view",
    "get_dashboard_revision",
    "get_target_card",
    "list_sidebar_items",
    "register_dashboard_routes",
]
