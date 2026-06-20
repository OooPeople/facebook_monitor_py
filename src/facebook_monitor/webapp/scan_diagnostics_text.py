"""Scan diagnostics text orchestration。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.webapp.scan_diagnostics_base_sections import (
    append_empty_runtime_status_line,
)
from facebook_monitor.webapp.scan_diagnostics_base_sections import append_latest_failed_scan_lines
from facebook_monitor.webapp.scan_diagnostics_base_sections import append_metadata_json_line
from facebook_monitor.webapp.scan_diagnostics_base_sections import append_outbox_summary_line
from facebook_monitor.webapp.scan_diagnostics_base_sections import append_runtime_state_lines
from facebook_monitor.webapp.scan_diagnostics_base_sections import append_scan_result_lines
from facebook_monitor.webapp.scan_diagnostics_base_sections import append_target_identity_lines
from facebook_monitor.webapp.scan_diagnostics_comments_sections import append_comments_meta
from facebook_monitor.webapp.scan_diagnostics_comments_sections import (
    format_comment_round_debug,
)
from facebook_monitor.webapp.scan_diagnostics_items import append_latest_scan_items
from facebook_monitor.webapp.scan_diagnostics_posts_sections import append_collected_meta
from facebook_monitor.webapp.scan_diagnostics_posts_sections import format_scan_round_debug
from facebook_monitor.webapp.scan_diagnostics_round_sections import append_rounds
from facebook_monitor.webapp.scan_diagnostics_sort_sections import (
    append_sort_diagnostics_block,
)


@dataclass(frozen=True)
class ScanDiagnosticsTextContext:
    """保存完整 scan diagnostics text 需要的輸入。"""

    target: TargetDescriptor
    config: TargetConfig
    runtime_state: TargetRuntimeState
    scan: ScanRun
    latest_scan_items: tuple[LatestScanItem, ...]
    notification_outbox_summary: NotificationOutboxSummary | None
    latest_failed_scan_run: ScanRun | None


def build_empty_scan_diagnostics_text(
    *,
    target: TargetDescriptor,
    runtime_state: TargetRuntimeState,
    notification_outbox_summary: NotificationOutboxSummary | None,
) -> str:
    """建立尚無掃描時的診斷文字。"""

    lines: list[str] = []
    append_target_identity_lines(lines, target)
    append_empty_runtime_status_line(lines, runtime_state)
    lines.append("scan_status=(none)")
    append_outbox_summary_line(lines, notification_outbox_summary)
    lines.append("note=尚無掃描診斷")
    return "\n".join(lines)


def build_completed_scan_diagnostics_text(
    context: ScanDiagnosticsTextContext,
) -> str:
    """建立已有 scan run 時的完整診斷文字，保留既有 section 順序。"""

    scan = context.scan
    metadata = scan.metadata or {}
    lines: list[str] = []
    append_target_identity_lines(lines, context.target)
    append_runtime_state_lines(lines, context.runtime_state)
    append_outbox_summary_line(lines, context.notification_outbox_summary)
    append_scan_result_lines(
        lines,
        scan=scan,
        config=context.config,
        metadata=metadata,
    )
    append_sort_diagnostics_block(lines, "sort_adjust", metadata.get("sort_adjust"))
    append_sort_diagnostics_block(lines, "comment_sort", metadata.get("comment_sort"))
    append_comments_meta(lines, metadata.get("comments_meta"))
    append_collected_meta(lines, metadata.get("collected_meta"))
    append_latest_failed_scan_lines(lines, context.latest_failed_scan_run)
    append_rounds(lines, "rounds", metadata.get("rounds"), format_scan_round_debug)
    append_rounds(
        lines,
        "comment_extract_rounds",
        metadata.get("comment_extract_rounds"),
        format_comment_round_debug,
    )
    append_latest_scan_items(lines, context.latest_scan_items)
    append_metadata_json_line(lines, metadata)
    return "\n".join(lines)
