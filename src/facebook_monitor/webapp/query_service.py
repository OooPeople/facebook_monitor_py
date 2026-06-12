"""Web UI read-side query facade.

本模組保留舊 import path；實作依職責拆到 dashboard / hit-record /
revision query modules。
"""

from __future__ import annotations

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.webapp.dashboard_queries import get_dashboard_view
from facebook_monitor.webapp.dashboard_queries import get_target_card
from facebook_monitor.webapp.dashboard_queries import list_sidebar_items
from facebook_monitor.webapp.dashboard_queries import list_target_rows
from facebook_monitor.webapp.dashboard_read_models import DashboardReadUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable
from facebook_monitor.webapp.dashboard_read_models import DashboardViewModel
from facebook_monitor.webapp.dashboard_read_models import DatabaseInvariantWarning
from facebook_monitor.webapp.dashboard_read_models import ProfileSessionWarning
from facebook_monitor.webapp.dashboard_revision_query import get_dashboard_revision
from facebook_monitor.webapp.hit_record_queries import count_hit_records
from facebook_monitor.webapp.hit_record_queries import list_full_hit_record_rows
from facebook_monitor.webapp.hit_record_queries import list_hit_record_preview_rows
from facebook_monitor.webapp.hit_record_queries import target_exists

__all__ = [
    "DashboardReadUnavailable",
    "DashboardRevision",
    "DashboardRevisionUnavailable",
    "DashboardViewModel",
    "DatabaseInvariantWarning",
    "ProfileSessionWarning",
    "SqliteApplicationContext",
    "count_hit_records",
    "get_dashboard_revision",
    "get_dashboard_view",
    "get_target_card",
    "list_full_hit_record_rows",
    "list_hit_record_preview_rows",
    "list_sidebar_items",
    "list_target_rows",
    "target_exists",
]
