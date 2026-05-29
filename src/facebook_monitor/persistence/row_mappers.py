"""SQLite row mappers。"""

from __future__ import annotations

import json
import sqlite3

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LegacyTargetConfig
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.persistence.sqlite_codec import decode_datetime
from facebook_monitor.persistence.sqlite_codec import decode_include_keyword_groups
from facebook_monitor.persistence.sqlite_codec import decode_keywords
from facebook_monitor.persistence.sqlite_codec import decode_runtime_status


def target_from_row(row: sqlite3.Row) -> TargetDescriptor:
    """將 SQLite row 轉為 TargetDescriptor。"""

    created_at = decode_datetime(row["created_at"])
    updated_at = decode_datetime(row["updated_at"])
    if created_at is None or updated_at is None:
        raise ValueError("target row has invalid datetime fields")
    return TargetDescriptor(
        id=row["id"],
        name=row["name"],
        target_kind=TargetKind(row["target_kind"]),
        group_id=row["group_id"],
        group_name=row["group_name"],
        group_cover_image_url=row["group_cover_image_url"],
        parent_post_id=row["parent_post_id"],
        scope_id=row["scope_id"],
        canonical_url=row["canonical_url"],
        metadata_status=TargetMetadataStatus(row["metadata_status"]),
        metadata_error=row["metadata_error"],
        enabled=bool(row["enabled"]),
        paused=bool(row["paused"]),
        worker_mode=WorkerMode(row["worker_mode"]),
        created_at=created_at,
        updated_at=updated_at,
    )


def target_config_from_row(row: sqlite3.Row, *, id_column: str) -> TargetConfig:
    """將 target config row 轉為 TargetConfig。"""

    return TargetConfig(
        target_id=row[id_column],
        include_keywords=decode_keywords(row["include_keywords"]),
        include_keyword_groups=(
            decode_include_keyword_groups(row["include_keyword_groups"])
            if _row_has_column(row, "include_keyword_groups")
            else ()
        ),
        exclude_keywords=decode_keywords(row["exclude_keywords"]),
        exclude_ignore_phrases=decode_keywords(row["exclude_ignore_phrases"]),
        min_refresh_sec=row["min_refresh_sec"],
        max_refresh_sec=row["max_refresh_sec"],
        jitter_enabled=bool(row["jitter_enabled"]),
        fixed_refresh_sec=row["fixed_refresh_sec"],
        max_items_per_scan=row["max_items_per_scan"],
        auto_load_more=bool(row["auto_load_more"]),
        auto_adjust_sort=bool(row["auto_adjust_sort"]),
        enable_desktop_notification=bool(row["enable_desktop_notification"]),
        enable_ntfy=bool(row["enable_ntfy"]),
        ntfy_topic=row["ntfy_topic"],
        enable_discord_notification=bool(row["enable_discord_notification"]),
        discord_webhook=row["discord_webhook"],
    )


def legacy_target_config_from_row(row: sqlite3.Row) -> LegacyTargetConfig:
    """將舊版 target_configs row 轉為 migration-only DTO。"""

    return LegacyTargetConfig(
        target_id=row["target_id"],
        include_keywords=decode_keywords(row["include_keywords"]),
        include_keyword_groups=(
            decode_include_keyword_groups(row["include_keyword_groups"])
            if _row_has_column(row, "include_keyword_groups")
            else ()
        ),
        exclude_keywords=decode_keywords(row["exclude_keywords"]),
        exclude_ignore_phrases=decode_keywords(row["exclude_ignore_phrases"]),
        min_refresh_sec=row["min_refresh_sec"],
        max_refresh_sec=row["max_refresh_sec"],
        jitter_enabled=bool(row["jitter_enabled"]),
        fixed_refresh_sec=row["fixed_refresh_sec"],
        max_items_per_scan=row["max_items_per_scan"],
        auto_load_more=bool(row["auto_load_more"]),
        auto_adjust_sort=bool(row["auto_adjust_sort"]),
        enable_desktop_notification=bool(row["enable_desktop_notification"]),
        enable_ntfy=bool(row["enable_ntfy"]),
        ntfy_topic=row["ntfy_topic"],
        enable_discord_notification=bool(row["enable_discord_notification"]),
        discord_webhook=row["discord_webhook"],
    )


def _row_has_column(row: sqlite3.Row, column_name: str) -> bool:
    """回傳 SQLite row 是否包含指定欄位。"""

    return column_name in row.keys()


def match_history_from_row(row: sqlite3.Row) -> MatchHistoryEntry:
    """將 SQLite row 轉為 MatchHistoryEntry。"""

    created_at = decode_datetime(row["created_at"])
    if created_at is None:
        raise ValueError("match history row has invalid created_at")
    return MatchHistoryEntry(
        target_id=row["target_id"],
        group_id=row["group_id"],
        group_name=row["group_name"],
        item_kind=ItemKind(row["item_kind"]),
        parent_post_id=row["parent_post_id"],
        comment_id=row["comment_id"],
        item_key=row["item_key"],
        author=row["author"],
        text=row["text"],
        permalink=row["permalink"],
        include_rule=row["include_rule"],
        timestamp_text=row["timestamp_text"],
        notified_at=decode_datetime(row["notified_at"]),
        created_at=created_at,
        include_rules=split_keyword_rule_text(row["include_rule"]),
    )


def scan_run_from_row(row: sqlite3.Row) -> ScanRun:
    """將 SQLite row 轉為 ScanRun。"""

    started_at = decode_datetime(row["started_at"])
    finished_at = decode_datetime(row["finished_at"])
    if started_at is None or finished_at is None:
        raise ValueError("scan run row has invalid datetime fields")
    return ScanRun(
        target_id=row["target_id"],
        status=ScanStatus(row["status"]),
        started_at=started_at,
        finished_at=finished_at,
        item_count=row["item_count"],
        matched_count=row["matched_count"],
        error_message=row["error_message"],
        worker_mode=WorkerMode(row["worker_mode"]),
        metadata=json.loads(row["metadata"] or "{}"),
    )


def latest_scan_item_from_row(row: sqlite3.Row) -> LatestScanItem:
    """將 SQLite row 轉為 LatestScanItem。"""

    scanned_at = decode_datetime(row["scanned_at"])
    if scanned_at is None:
        raise ValueError("latest scan item row has invalid scanned_at")
    return LatestScanItem(
        target_id=row["target_id"],
        scan_run_id=row["scan_run_id"],
        item_kind=ItemKind(row["item_kind"]),
        item_key=row["item_key"],
        item_index=row["item_index"],
        author=row["author"],
        text=row["text"],
        permalink=row["permalink"],
        matched_keyword=row["matched_keyword"],
        matched_keywords=split_keyword_rule_text(row["matched_keyword"]),
        debug_metadata=json.loads(row["debug_metadata"] or "{}"),
        scanned_at=scanned_at,
    )


def runtime_state_from_row(row: sqlite3.Row) -> TargetRuntimeState:
    """將 SQLite row 轉為 TargetRuntimeState。"""

    updated_at = decode_datetime(row["updated_at"])
    if updated_at is None:
        raise ValueError("target runtime state row has invalid updated_at")
    return TargetRuntimeState(
        target_id=row["target_id"],
        desired_state=TargetDesiredState(row["desired_state"]),
        runtime_status=decode_runtime_status(row["runtime_status"]),
        scan_requested_at=decode_datetime(row["scan_requested_at"]),
        last_enqueued_at=decode_datetime(row["last_enqueued_at"]),
        last_started_at=decode_datetime(row["last_started_at"]),
        last_finished_at=decode_datetime(row["last_finished_at"]),
        last_heartbeat_at=decode_datetime(row["last_heartbeat_at"]),
        last_error=row["last_error"],
        last_skip_reason=row["last_skip_reason"],
        enqueue_reason=row["enqueue_reason"],
        active_worker_id=row["active_worker_id"],
        active_page_id=row["active_page_id"],
        last_page_reloaded_at=decode_datetime(row["last_page_reloaded_at"]),
        scan_guard_count=row["scan_guard_count"],
        display_next_due_at=decode_datetime(row["display_next_due_at"]),
        consecutive_failure_reason=row["consecutive_failure_reason"],
        consecutive_failure_count=row["consecutive_failure_count"],
        updated_at=updated_at,
    )


def notification_event_from_row(row: sqlite3.Row) -> NotificationEvent:
    """將 SQLite row 轉為 NotificationEvent。"""

    created_at = decode_datetime(row["created_at"])
    if created_at is None:
        raise ValueError("notification event row has invalid created_at")
    return NotificationEvent(
        target_id=row["target_id"],
        item_key=row["item_key"],
        channel=NotificationChannel(row["channel"]),
        status=NotificationStatus(row["status"]),
        message=row["message"],
        event_kind=NotificationEventKind(row["event_kind"]),
        source_scan_run_id=row["source_scan_run_id"],
        failure_reason=row["failure_reason"],
        failure_count=int(row["failure_count"]),
        created_at=created_at,
    )


def notification_outbox_from_row(row: sqlite3.Row) -> NotificationOutboxEntry:
    """將 SQLite row 轉為 NotificationOutboxEntry。"""

    created_at = decode_datetime(row["created_at"])
    updated_at = decode_datetime(row["updated_at"])
    if created_at is None or updated_at is None:
        raise ValueError("notification outbox row has invalid datetime fields")
    return NotificationOutboxEntry(
        id=int(row["id"]),
        idempotency_key=row["idempotency_key"],
        target_id=row["target_id"],
        item_key=row["item_key"],
        item_kind=ItemKind(row["item_kind"]),
        channel=NotificationChannel(row["channel"]),
        status=NotificationOutboxStatus(row["status"]),
        title=row["title"],
        message=row["message"],
        endpoint=row["endpoint"],
        permalink=row["permalink"],
        event_kind=NotificationEventKind(row["event_kind"]),
        source_scan_run_id=row["source_scan_run_id"],
        failure_reason=row["failure_reason"],
        failure_count=int(row["failure_count"]),
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        notification_event_id=row["notification_event_id"],
        created_at=created_at,
        updated_at=updated_at,
    )
