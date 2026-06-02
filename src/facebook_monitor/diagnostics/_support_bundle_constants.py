"""Support bundle shared constants。

職責：集中 support bundle zip contract 與 bounded collection limits，避免
facade 與 collectors 之間互相 import。
"""

from __future__ import annotations

SUPPORT_BUNDLE_FILENAME_PREFIX = "facebook-monitor-support-"
SUPPORT_BUNDLE_FILENAME_SUFFIX = ".zip"
SUPPORT_BUNDLE_SCHEMA_VERSION = 2
LOG_TAIL_FILE_NAMES = ("app.log", "error.log", "startup.log", "updater.log")
LOG_TAIL_MAX_BYTES = 64 * 1024
LOG_TAIL_MAX_LINES = 200
RECENT_SCAN_LIMIT_PER_TARGET = 5
LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET = 5
RECENT_NOTIFICATION_SAMPLE_LIMIT = 20
