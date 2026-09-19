"""SQLite schema 產品語義 contract。

職責：集中目前 DB 欄位允許的 enum、boolean 與 range 規則。這些規則供
read-only invariant checker、測試與分批導入的正式 CHECK constraints 對照。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from facebook_monitor.core.facebook_access import FacebookAccessCircuitStatus
from facebook_monitor.core.facebook_access import FacebookAccessEventKind
from facebook_monitor.core.facebook_access import FacebookActionKind
from facebook_monitor.core.facebook_access import FacebookProbeResult
from facebook_monitor.core.facebook_access import FacebookProductOperationKind
from facebook_monitor.core.facebook_access import FacebookRecoveryRecipeKind
from facebook_monitor.core.facebook_access import FacebookWorkSourceKind
from facebook_monitor.core.facebook_session_recovery import FacebookSessionRecoveryStatus
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationDedupeStatus
from facebook_monitor.core.models import NotificationEventKind
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


@dataclass(frozen=True)
class SchemaDatetimeContract:
    """描述一張表中應維持可解析 ISO datetime 的欄位。"""

    table: str
    row_id_column: str
    fields: tuple[str, ...]
    required_fields: tuple[str, ...] = ()


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
    SchemaEnumContract("logical_items", "id", "item_kind", _enum_values(ItemKind)),
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
        "notification_events",
        "id",
        "event_kind",
        _enum_values(NotificationEventKind),
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
        "notification_outbox",
        "id",
        "event_kind",
        _enum_values(NotificationEventKind),
    ),
    SchemaEnumContract(
        "notification_dedupe",
        "id",
        "event_kind",
        _enum_values(NotificationEventKind),
    ),
    SchemaEnumContract(
        "notification_dedupe",
        "id",
        "channel",
        _enum_values(NotificationChannel),
    ),
    SchemaEnumContract(
        "notification_dedupe",
        "id",
        "item_kind",
        _enum_values(ItemKind),
    ),
    SchemaEnumContract(
        "notification_dedupe",
        "id",
        "status",
        _enum_values(NotificationDedupeStatus),
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
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "state",
        _enum_values(FacebookAccessCircuitStatus),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "source_kind",
        _enum_values(FacebookWorkSourceKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "operation_kind",
        _enum_values(FacebookProductOperationKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "trigger_action_kind",
        _enum_values(FacebookActionKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "recovery_recipe_kind",
        _enum_values(FacebookRecoveryRecipeKind),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "requested_recipe_kind",
        _enum_values(FacebookRecoveryRecipeKind),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_state",
        "'profile'",
        "last_probe_result",
        _enum_values(FacebookProbeResult),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "event_kind",
        _enum_values(FacebookAccessEventKind),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "from_state",
        _enum_values(FacebookAccessCircuitStatus),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "to_state",
        _enum_values(FacebookAccessCircuitStatus),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "source_kind",
        _enum_values(FacebookWorkSourceKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "operation_kind",
        _enum_values(FacebookProductOperationKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "trigger_action_kind",
        _enum_values(FacebookActionKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_access_circuit_events",
        "id",
        "recovery_recipe_kind",
        _enum_values(FacebookRecoveryRecipeKind),
    ),
    SchemaEnumContract(
        "facebook_session_recovery_state",
        "'profile'",
        "status",
        _enum_values(FacebookSessionRecoveryStatus),
    ),
    SchemaEnumContract(
        "facebook_session_recovery_state",
        "'profile'",
        "requested_operation_kind",
        _enum_values(FacebookProductOperationKind) | frozenset({""}),
    ),
    SchemaEnumContract(
        "facebook_session_recovery_state",
        "'profile'",
        "requested_recipe_kind",
        _enum_values(FacebookRecoveryRecipeKind),
    ),
    SchemaEnumContract(
        "facebook_session_recovery_state",
        "'profile'",
        "last_probe_result",
        _enum_values(FacebookProbeResult),
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
    SchemaRangeContract(
        "notification_outbox",
        "id",
        "attempts",
        "attempts < 0",
    ),
    SchemaRangeContract(
        "notification_outbox",
        "id",
        "failure_count",
        "failure_count < 0",
    ),
    SchemaRangeContract(
        "notification_events",
        "id",
        "failure_count",
        "failure_count < 0",
    ),
    SchemaRangeContract(
        "target_dedupe_state",
        "target_id",
        "dedupe_epoch",
        "dedupe_epoch < 0",
    ),
    SchemaRangeContract(
        "logical_items",
        "id",
        "dedupe_epoch",
        "dedupe_epoch < 0",
    ),
    SchemaRangeContract(
        "logical_item_aliases",
        "id",
        "dedupe_epoch",
        "dedupe_epoch < 0",
    ),
    SchemaRangeContract(
        "notification_dedupe",
        "id",
        "dedupe_epoch",
        "dedupe_epoch < 0",
    ),
    SchemaRangeContract(
        "notification_dedupe",
        "id",
        "failure_count",
        "failure_count < 0",
    ),
    SchemaRangeContract(
        "target_runtime_state",
        "target_id",
        "scan_guard_count",
        (
            "scan_guard_count < 0 OR consecutive_failure_count < 0 "
            "OR consecutive_scan_skip_count < 0"
        ),
    ),
    SchemaRangeContract(
        "facebook_access_circuit_state",
        "'profile'",
        "circuit_counts",
        "generation < 0 OR detection_count < 0 OR reopen_count < 0",
    ),
    SchemaRangeContract(
        "facebook_access_circuit_events",
        "id",
        "policy_delay_seconds",
        "policy_delay_seconds < 0",
    ),
    SchemaRangeContract(
        "facebook_automation_pacing_state",
        "'profile'",
        "lease_generation",
        "lease_generation < 0",
    ),
    SchemaRangeContract(
        "facebook_session_recovery_state",
        "'profile'",
        "generation",
        "generation < 1",
    ),
)


DATETIME_CONTRACTS: tuple[SchemaDatetimeContract, ...] = (
    SchemaDatetimeContract(
        "targets",
        "id",
        ("created_at", "updated_at"),
        required_fields=("created_at", "updated_at"),
    ),
    SchemaDatetimeContract(
        "match_history",
        "id",
        ("recorded_at", "created_at"),
        required_fields=("created_at",),
    ),
    SchemaDatetimeContract(
        "latest_scan_items",
        "target_id || ':' || item_key",
        ("scanned_at",),
        required_fields=("scanned_at",),
    ),
    SchemaDatetimeContract(
        "scan_runs",
        "id",
        ("started_at", "finished_at"),
        required_fields=("started_at", "finished_at"),
    ),
    SchemaDatetimeContract(
        "notification_events",
        "id",
        ("created_at",),
        required_fields=("created_at",),
    ),
    SchemaDatetimeContract(
        "notification_outbox",
        "id",
        ("created_at", "updated_at"),
        required_fields=("created_at", "updated_at"),
    ),
    SchemaDatetimeContract(
        "target_runtime_state",
        "target_id",
        (
            "scan_requested_at",
            "last_enqueued_at",
            "last_started_at",
            "last_finished_at",
            "last_heartbeat_at",
            "last_page_reloaded_at",
            "display_next_due_at",
            "updated_at",
        ),
        required_fields=("updated_at",),
    ),
    SchemaDatetimeContract(
        "target_cover_image_refresh_state",
        "target_id",
        (
            "requested_at",
            "last_attempted_at",
            "last_succeeded_at",
            "last_failed_at",
            "updated_at",
        ),
        required_fields=("updated_at",),
    ),
    SchemaDatetimeContract(
        "sidebar_groups",
        "id",
        ("created_at", "updated_at"),
        required_fields=("created_at", "updated_at"),
    ),
    SchemaDatetimeContract(
        "sidebar_target_placements",
        "target_id",
        ("updated_at",),
        required_fields=("updated_at",),
    ),
    SchemaDatetimeContract(
        "sidebar_group_config_templates",
        "sidebar_group_id",
        ("updated_at",),
        required_fields=("updated_at",),
    ),
    SchemaDatetimeContract(
        "facebook_access_circuit_state",
        "'profile'",
        (
            "opened_at",
            "last_detected_at",
            "cooldown_until",
            "half_open_started_at",
            "half_open_lease_expires_at",
            "probe_requested_at",
            "last_probe_finished_at",
            "closed_at",
            "updated_at",
        ),
        required_fields=("updated_at",),
    ),
    SchemaDatetimeContract(
        "facebook_access_circuit_events",
        "id",
        ("occurred_at",),
        required_fields=("occurred_at",),
    ),
    SchemaDatetimeContract(
        "facebook_automation_pacing_state",
        "'profile'",
        (
            "active_lease_expires_at",
            "last_automation_started_at",
            "last_automation_finished_at",
            "next_automation_not_before",
            "updated_at",
        ),
        required_fields=("updated_at",),
    ),
    SchemaDatetimeContract(
        "managed_profile_identity_binding",
        "id",
        ("bound_at", "updated_at"),
        required_fields=("bound_at", "updated_at"),
    ),
    SchemaDatetimeContract(
        "facebook_session_recovery_state",
        "'profile'",
        (
            "stale_detected_at",
            "earliest_probe_at",
            "request_requested_at",
            "probe_started_at",
            "probe_lease_expires_at",
            "last_probe_finished_at",
            "recovered_at",
            "updated_at",
        ),
        required_fields=("stale_detected_at", "earliest_probe_at", "updated_at"),
    ),
)
