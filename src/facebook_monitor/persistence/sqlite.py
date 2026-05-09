"""SQLite persistence compatibility exports。

職責：保留舊有 `facebook_monitor.persistence.sqlite` 匯入相容性。
實作已拆到 connection、schema、row_mappers 與 repositories modules。
"""

from __future__ import annotations

from facebook_monitor.persistence.repositories.global_notification_settings import (
    GlobalNotificationSettingsRepository,
)
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.match_history import MatchHistoryRepository
from facebook_monitor.persistence.repositories.notification_events import NotificationEventRepository
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.repositories.scan_runs import ScanRunRepository
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.schema import ensure_column
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import decode_keywords
from facebook_monitor.persistence.sqlite_codec import decode_runtime_status
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.persistence.sqlite_codec import encode_keywords
from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.persistence.sqlite_codec import write_schema_version
from facebook_monitor.persistence.sqlite_connection import SqliteConnection


__all__ = [
    "GlobalNotificationSettingsRepository",
    "LatestScanItemRepository",
    "MatchHistoryRepository",
    "NotificationEventRepository",
    "NotificationOutboxRepository",
    "SCHEMA_VERSION",
    "ScanRunRepository",
    "SeenItemRepository",
    "SqliteConnection",
    "TargetConfigRepository",
    "TargetRepository",
    "TargetRuntimeStateRepository",
    "decode_datetime",
    "decode_keywords",
    "decode_runtime_status",
    "encode_datetime",
    "encode_keywords",
    "ensure_column",
    "initialize_schema",
    "read_schema_version",
    "write_schema_version",
]
