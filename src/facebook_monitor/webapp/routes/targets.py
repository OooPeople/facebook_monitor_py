"""Target management route aggregator。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from facebook_monitor.webapp.routes.target_actions import register_target_action_routes
from facebook_monitor.webapp.routes.target_config import register_target_config_routes
from facebook_monitor.webapp.routes.target_create import register_create_target_routes
from facebook_monitor.webapp.routes.target_metadata import register_target_metadata_routes
from facebook_monitor.webapp.routes.target_notifications import register_target_notification_routes


def register_target_routes(app: FastAPI, templates: Jinja2Templates) -> None:
    """註冊 target create/update/action routes。"""

    register_create_target_routes(app, templates)
    register_target_config_routes(app)
    register_target_metadata_routes(app)
    register_target_notification_routes(app)
    register_target_action_routes(app)
