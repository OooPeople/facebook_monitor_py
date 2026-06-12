"""Sidebar API route aggregator."""

from __future__ import annotations

from fastapi import FastAPI

from facebook_monitor.webapp.routes.sidebar_group_actions import (
    register_sidebar_group_action_routes,
)
from facebook_monitor.webapp.routes.sidebar_groups import register_sidebar_group_routes
from facebook_monitor.webapp.routes.sidebar_layout import register_sidebar_layout_routes
from facebook_monitor.webapp.routes.sidebar_templates import register_sidebar_template_routes


def register_sidebar_routes(app: FastAPI) -> None:
    """註冊 sidebar layout、group、group action 與 template routes。"""

    register_sidebar_layout_routes(app)
    register_sidebar_group_routes(app)
    register_sidebar_group_action_routes(app)
    register_sidebar_template_routes(app)
