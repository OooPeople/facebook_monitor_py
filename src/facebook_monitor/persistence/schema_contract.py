"""SQLite schema 產品語義 contract。

職責：集中目前 DB 欄位允許的 enum、boolean 與 range 規則。這些規則供
read-only invariant checker 與測試共用；正式 CHECK constraint 仍需透過
明確 migration 分批導入。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS


@dataclass(frozen=True)
class SchemaEnumContract:
    """描述單一 DB enum 欄位允許值。"""

    table: str
    row_id_expr: str
    field: str
    allowed_values: frozenset[str]


@dataclass(frozen=True)
class SchemaBooleanContract:
    """描述一張表中應維持 0/1 的 boolean 欄位。"""

    table: str
    row_id_column: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class SchemaRangeContract:
    """描述一個 DB range invariant 查詢條件。"""

    table: str
    row_id_column: str
    field: str
    where_clause: str
    params: tuple[Any, ...] = ()


def _enum_values(enum_type: type[StrEnum]) -> frozenset[str]:
    """回傳 StrEnum values。"""

    return frozenset(item.value for item in enum_type)


ENUM_CONTRACTS: tuple[SchemaEnumContract, ...] = (
    SchemaEnumContract("targets", "id", "target_kind", _enum_values(TargetKind)),
    SchemaEnumContract("targets", "id", "metadata_status", _enum_values(TargetMetadataStatus)),
    SchemaEnumContract("targets", "id", "worker_mode", _enum_values(WorkerMode)),
    SchemaEnumContract(
        "seen_items",
        "scope_id || ':' || item_key",
        "item_kind",
        _enum_values(ItemKind),
    ),
    SchemaEnumContract("match_history", "id", "item_kind", _enum_values(ItemKind)),
    SchemaEnumContract(
        "latest_scan_items",
        "target_id || ':' || item_key",
        "item_kind",
        _enum_values(ItemKind),
    ),
    SchemaEnumContract("scan_runs", "id", "status", _enum_values(ScanStatus)),
    SchemaEnumContract("scan_runs", "id", "worker_mode", _enum_values(WorkerMode)),
    SchemaEnumContract(
        "notification_events",
        "id",
        "channel",
        _enum_values(NotificationChannel),
    ),
    SchemaEnumContract(
        "notification_events",
        "id",
        "status",
        _enum_values(NotificationStatus),
    ),
    SchemaEnumContract(
        "notification_outbox",
        "id",
        "item_kind",
        _enum_values(ItemKind),
    ),
    SchemaEnumContract(
        "notification_outbox",
        "id",
        "channel",
        _enum_values(NotificationChannel),
    ),
    SchemaEnumContract(
        "notification_outbox",
        "id",
        "status",
        _enum_values(NotificationOutboxStatus),
    ),
    SchemaEnumContract(
        "target_runtime_state",
        "target_id",
        "desired_state",
        _enum_values(TargetDesiredState),
    ),
    SchemaEnumContract(
        "target_runtime_state",
        "target_id",
        "runtime_status",
        _enum_values(TargetRuntimeStatus),
    ),
    SchemaEnumContract(
        "target_cover_image_refresh_state",
        "target_id",
        "status",
        _enum_values(TargetCoverImageRefreshStatus),
    ),
    SchemaEnumContract(
        "target_cover_image_refresh_state",
        "target_id",
        "last_result",
        _enum_values(TargetCoverImageRefreshResult) | frozenset({""}),
    ),
)


BOOLEAN_CONTRACTS: tuple[SchemaBooleanContract, ...] = (
    SchemaBooleanContract("targets", "id", ("enabled", "paused")),
    SchemaBooleanContract(
        "target_configs",
        "target_id",
        (
            "jitter_enabled",
            "auto_load_more",
            "auto_adjust_sort",
            "enable_desktop_notification",
            "enable_ntfy",
            "enable_discord_notification",
        ),
    ),
    SchemaBooleanContract("scan_scope_state", "scope_id", ("initialized",)),
    SchemaBooleanContract(
        "global_notification_settings",
        "id",
        ("enable_desktop_notification", "enable_ntfy", "enable_discord_notification"),
    ),
    SchemaBooleanContract("sidebar_groups", "id", ("collapsed",)),
    SchemaBooleanContract(
        "sidebar_group_config_templates",
        "sidebar_group_id",
        (
            "jitter_enabled",
            "auto_load_more",
            "auto_adjust_sort",
            "enable_desktop_notification",
            "enable_ntfy",
            "enable_discord_notification",
        ),
    ),
    SchemaBooleanContract("target_cover_image_refresh_state", "target_id", ("changed",)),
)


RANGE_CONTRACTS: tuple[SchemaRangeContract, ...] = (
    SchemaRangeContract(
        "target_configs",
        "target_id",
        "refresh_range",
        "min_refresh_sec < ? OR max_refresh_sec < ? OR min_refresh_sec > max_refresh_sec",
        (MIN_REFRESH_SECONDS, MIN_REFRESH_SECONDS),
    ),
    SchemaRangeContract(
        "sidebar_group_config_templates",
        "sidebar_group_id",
        "refresh_range",
        "min_refresh_sec < ? OR max_refresh_sec < ? OR min_refresh_sec > max_refresh_sec",
        (MIN_REFRESH_SECONDS, MIN_REFRESH_SECONDS),
    ),
    SchemaRangeContract(
        "target_configs",
        "target_id",
        "max_items_per_scan",
        "max_items_per_scan <= 0",
    ),
    SchemaRangeContract(
        "sidebar_group_config_templates",
        "sidebar_group_id",
        "max_items_per_scan",
        "max_items_per_scan <= 0",
    ),
    SchemaRangeContract("scan_runs", "id", "item_count", "item_count < 0 OR matched_count < 0"),
    SchemaRangeContract("notification_outbox", "id", "attempts", "attempts < 0"),
    SchemaRangeContract(
        "target_runtime_state",
        "target_id",
        "scan_guard_count",
        "scan_guard_count < 0 OR consecutive_failure_count < 0",
    ),
)
