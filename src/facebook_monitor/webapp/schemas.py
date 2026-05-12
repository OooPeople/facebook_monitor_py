"""Web UI view model compatibility exports。

職責：保留既有 import path，實際 view models 已依職責拆到鄰近模組。
"""

from __future__ import annotations

from facebook_monitor.webapp.dashboard_models import SidebarTargetItem
from facebook_monitor.webapp.dashboard_models import TargetRow
from facebook_monitor.webapp.dashboard_presenters import SettingsSummary
from facebook_monitor.webapp.dashboard_presenters import TargetCardSummary
from facebook_monitor.webapp.hit_record_models import FullHitRecordRow
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.preview_models import TargetPreviewRow
from facebook_monitor.webapp.preview_models import trim_preview_text

__all__ = [
    "FullHitRecordRow",
    "HitRecordPreviewRow",
    "LatestScanItemRow",
    "SettingsSummary",
    "SidebarTargetItem",
    "TargetCardSummary",
    "TargetPreviewRow",
    "TargetRow",
    "trim_preview_text",
]
