"""Facebook sort DOM JavaScript payload exports。

職責：保留既有 import path；實際 browser-side payload 已拆到
current-label 與 adjustment fallback 專用模組。
"""

from __future__ import annotations

from facebook_monitor.facebook.sort_adjust_scripts import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_adjust_scripts import FEED_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_current_label_scripts import COMMENT_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_current_label_scripts import FEED_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_current_label_scripts import SORT_MENU_CANDIDATE_TEXTS_SCRIPT


__all__ = [
    "COMMENT_SORT_ADJUST_SCRIPT",
    "COMMENT_SORT_CURRENT_LABEL_SCRIPT",
    "FEED_SORT_ADJUST_SCRIPT",
    "FEED_SORT_CURRENT_LABEL_SCRIPT",
    "SORT_MENU_CANDIDATE_TEXTS_SCRIPT",
]
