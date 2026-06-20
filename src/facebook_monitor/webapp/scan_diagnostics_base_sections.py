"""Scan diagnostics 基礎文字 section formatter。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_runtime_skip_message
from facebook_monitor.webapp.scan_reason_presenters import format_scan_failure_reason
from facebook_monitor.webapp.scan_reason_presenters import format_scan_stop_reason
from facebook_monitor.webapp.time_presenters import format_datetime_for_ui
from facebook_monitor.webapp.time_presenters import format_optional_datetime_for_ui


def append_target_identity_lines(
    lines: list[str],
    target: TargetDescriptor,
) -> None:
    """附加 target identity diagnostics。"""

    lines.extend(
        [
            f"target_id={target.id}",
            f"target_kind={target.target_kind.value}",
            f"group_id={target.group_id}",
            f"parent_post_id={target.parent_post_id or '(none)'}",
            f"scope_id={target.scope_id}",
        ]
    )


def append_empty_runtime_status_line(
    lines: list[str],
    runtime_state: TargetRuntimeState,
) -> None:
    """附加尚無掃描時使用的精簡 runtime status。"""

    lines.append(f"runtime_status={runtime_state.runtime_status.value}")


def append_runtime_state_lines(
    lines: list[str],
    runtime_state: TargetRuntimeState,
) -> None:
    """附加完整 runtime state diagnostics。"""

    last_page_reloaded_at = format_optional_datetime_for_ui(
        runtime_state.last_page_reloaded_at
    )
    last_enqueued_at = format_optional_datetime_for_ui(runtime_state.last_enqueued_at)
    last_started_at = format_optional_datetime_for_ui(runtime_state.last_started_at)
    last_finished_at = format_optional_datetime_for_ui(runtime_state.last_finished_at)
    lines.extend(
        [
            f"runtime_status={runtime_state.runtime_status.value}",
            f"queued={runtime_state.queued}",
            f"running={runtime_state.running}",
            f"active_worker_id={runtime_state.active_worker_id or '(none)'}",
            f"active_page_id={runtime_state.active_page_id or '(none)'}",
            f"last_page_reloaded_at={last_page_reloaded_at}",
            f"enqueue_reason={runtime_state.enqueue_reason or '(none)'}",
            f"last_enqueued_at={last_enqueued_at}",
            f"last_started_at={last_started_at}",
            f"last_finished_at={last_finished_at}",
            f"scan_guard_count={runtime_state.scan_guard_count}",
            "last_skip_reason="
            + (
                format_runtime_skip_message(runtime_state.last_skip_reason)
                if runtime_state.last_skip_reason
                else "(none)"
            ),
        ]
    )


def append_outbox_summary_line(
    lines: list[str],
    summary: NotificationOutboxSummary | None,
) -> None:
    """附加 target-scoped outbox backlog 診斷摘要。"""

    lines.append(format_outbox_summary_line(summary))


def format_outbox_summary_line(summary: NotificationOutboxSummary | None) -> str:
    """格式化 target-scoped outbox backlog 診斷摘要。"""

    if summary is None:
        return "outbox=(unavailable)"
    oldest_pending_at = format_optional_datetime_for_ui(
        summary.oldest_pending_updated_at
    )
    return (
        "outbox="
        f"pending:{summary.pending_count},"
        f"processing:{summary.processing_count},"
        f"failed:{summary.failed_count},"
        f"terminal:{summary.terminal_count},"
        f"oldest_pending:{oldest_pending_at},"
        f"max_attempts:{summary.max_attempts}"
    )


def append_scan_result_lines(
    lines: list[str],
    *,
    scan: ScanRun,
    config: TargetConfig,
    metadata: Mapping[str, Any],
) -> None:
    """附加 scan result diagnostics，保留 failed-only 欄位與 fallback 文案。"""

    scan_failed = scan.status == ScanStatus.FAILED
    scan_lines = [
        f"scan_status={scan.status.value}",
        f"failure_reason={format_scan_failure_reason(str(metadata.get('reason') or ''))}"
        if scan_failed
        else "",
        f"retryable={metadata.get('retryable', '(unknown)')}"
        if scan_failed
        else "",
        f"runtime_action={metadata.get('runtime_action', '(unknown)')}"
        if scan_failed
        else "",
        f"retry_streak={metadata.get('retry_streak', '(none)')}"
        if scan_failed
        else "",
        f"retry_limit={metadata.get('retry_limit', '(none)')}"
        if scan_failed
        else "",
        f"finished_at={format_datetime_for_ui(scan.finished_at)}",
        f"item_count={scan.item_count}",
        f"matched_count={scan.matched_count}",
        f"new_count={metadata.get('new_count', '(unknown)')}",
        f"target_count={metadata.get('target_count', config.max_items_per_scan)}",
        f"candidate_count={metadata.get('candidate_count', scan.item_count)}",
        f"round_count={metadata.get('round_count', 0)}",
        f"max_window_count={metadata.get('max_window_count', '(unknown)')}",
        f"requested_scroll_rounds={metadata.get('requested_scroll_rounds', '(unknown)')}",
        f"scroll_rounds={metadata.get('scroll_rounds', '(unknown)')}",
        f"scroll_wait_ms={metadata.get('scroll_wait_ms', '(unknown)')}",
        f"collection_strategy={metadata.get('collection_strategy', '(unknown)')}",
        f"auto_load_more={metadata.get('auto_load_more', '(unknown)')}",
        f"load_more_mode={metadata.get('load_more_mode', '(unknown)')}",
        f"scroll_collection_enabled={metadata.get('scroll_collection_enabled', '(unknown)')}",
        f"stop_reason={format_scan_stop_reason(str(metadata.get('stop_reason') or ''))}",
        f"worker={metadata.get('worker', '(unknown)')}",
    ]
    lines.extend(line for line in scan_lines if line)


def append_latest_failed_scan_lines(
    lines: list[str],
    latest_failed_scan_run: ScanRun | None,
) -> None:
    """附加最近 failed scan 區塊。"""

    if latest_failed_scan_run is None:
        return
    lines.extend(
        [
            "",
            "latest_failed_scan:",
            f"finished_at={format_datetime_for_ui(latest_failed_scan_run.finished_at)}",
            "reason="
            + format_scan_failure_reason(
                str((latest_failed_scan_run.metadata or {}).get("reason") or "")
            ),
            "error="
            + (
                format_failure_message_text(latest_failed_scan_run.error_message)
                if latest_failed_scan_run.error_message
                else "(none)"
            ),
        ]
    )


def append_metadata_json_line(
    lines: list[str],
    metadata: Mapping[str, Any],
) -> None:
    """附加完整 scan metadata JSON；此行必須維持最後一行。"""

    metadata_json = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
    )
    lines.append("metadata_json=" + metadata_json)
