"""Web UI scan diagnostics presenter。

職責：把 worker scan metadata 轉成 UI 可顯示或複製的診斷 view，
避免 schema model 直接散落 posts/comments metadata key 格式化邏輯。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationOutboxSummary
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.user_messages import format_failure_message_text
from facebook_monitor.core.user_messages import format_failure_reason as _format_failure_reason
from facebook_monitor.core.user_messages import format_runtime_skip_message


_LATEST_SCAN_ITEM_DEBUG_KEYS = (
    "source",
    "containerRole",
    "firstSeenRound",
    "roundItemIndex",
    "collectionIndex",
    "domIndex",
    "domPosition",
    "textSource",
    "textDiagnostics",
    "textLength",
    "displayTextLength",
    "rawTextLength",
    "rawDisplayTextLength",
    "permalinkSource",
    "canonicalPermalinkCandidateCount",
    "postId",
    "postIdSource",
    "parentPostId",
    "commentId",
    "commentIdSource",
    "commentAnchorHref",
    "routePostId",
    "routePostIdMatchesTarget",
    "routePostIdSource",
    "commentScopeReason",
    "commentSearchRoot",
    "commentSearchRootStrategy",
    "currentRoutePostId",
    "currentRouteMatchesTarget",
    "linkCount",
    "linkDiagnostics",
    "hasStoryMessage",
    "hasCommentPermalink",
    "warmupAttempted",
    "warmupResolved",
    "warmupCandidateCount",
    "warmupDiagnostics",
    "expandAttempted",
    "expandCount",
    "classification",
)
_SORT_DIAGNOSTIC_KEYS = (
    "method",
    "target_kind",
    "fallback_used",
    "fallback_recovery",
    "failure_stage",
    "native_attempted",
    "native_failure_stage",
    "native_exception_class",
    "native_after_label",
    "control_candidate_count",
    "control_locator",
    "menu_opened",
    "menu_role",
    "preferred_option_count",
    "option_locator",
    "clicked_option_text",
    "confirm_timeout_ms",
)


@dataclass(frozen=True)
class ScanDiagnosticsView:
    """保存 target 最近掃描診斷顯示資料。"""

    summary: str
    text: str


def build_scan_diagnostics_view(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    runtime_state: TargetRuntimeState,
    latest_scan_run: ScanRun | None,
    latest_scan_items: tuple[LatestScanItem, ...] = (),
    notification_outbox_summary: NotificationOutboxSummary | None = None,
    latest_failed_scan_run: ScanRun | None = None,
) -> ScanDiagnosticsView:
    """建立 target card 使用的 scan diagnostics view。"""

    if latest_scan_run is None:
        return ScanDiagnosticsView(
            summary="尚無掃描診斷",
            text="\n".join(
                [
                    f"target_id={target.id}",
                    f"target_kind={target.target_kind.value}",
                    f"group_id={target.group_id}",
                    f"parent_post_id={target.parent_post_id or '(none)'}",
                    f"scope_id={target.scope_id}",
                    f"runtime_status={runtime_state.runtime_status.value}",
                    "scan_status=(none)",
                    _format_outbox_summary_line(notification_outbox_summary),
                    "note=尚無掃描診斷",
                ]
            ),
        )

    metadata = latest_scan_run.metadata or {}
    if latest_scan_run.status == ScanStatus.FAILED:
        failure_reason = format_scan_failure_reason(str(metadata.get("reason") or ""))
        return ScanDiagnosticsView(
            summary=f"status=failed · reason={failure_reason}",
            text=_build_scan_diagnostics_text(
                target=target,
                config=config,
                runtime_state=runtime_state,
                scan=latest_scan_run,
                latest_scan_items=latest_scan_items,
                notification_outbox_summary=notification_outbox_summary,
                latest_failed_scan_run=latest_failed_scan_run,
            ),
        )
    round_count = metadata.get("round_count", 0)
    candidate_count = metadata.get("candidate_count", latest_scan_run.item_count)
    stop_reason = format_scan_stop_reason(str(metadata.get("stop_reason") or ""))
    return ScanDiagnosticsView(
        summary=f"rounds={round_count} · candidates={candidate_count} · stop={stop_reason}",
        text=_build_scan_diagnostics_text(
            target=target,
            config=config,
            runtime_state=runtime_state,
            scan=latest_scan_run,
            latest_scan_items=latest_scan_items,
            notification_outbox_summary=notification_outbox_summary,
            latest_failed_scan_run=latest_failed_scan_run,
        ),
    )


def _build_scan_diagnostics_text(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    runtime_state: TargetRuntimeState,
    scan: ScanRun,
    latest_scan_items: tuple[LatestScanItem, ...],
    notification_outbox_summary: NotificationOutboxSummary | None,
    latest_failed_scan_run: ScanRun | None,
) -> str:
    """建立可複製的 scan-level diagnostics 文字。"""

    metadata = scan.metadata or {}
    lines = [
        f"target_id={target.id}",
        f"target_kind={target.target_kind.value}",
        f"group_id={target.group_id}",
        f"parent_post_id={target.parent_post_id or '(none)'}",
        f"scope_id={target.scope_id}",
        f"runtime_status={runtime_state.runtime_status.value}",
        f"queued={runtime_state.queued}",
        f"running={runtime_state.running}",
        f"active_worker_id={runtime_state.active_worker_id or '(none)'}",
        f"active_page_id={runtime_state.active_page_id or '(none)'}",
        f"last_page_reloaded_at={format_optional_datetime_for_ui(runtime_state.last_page_reloaded_at)}",
        f"enqueue_reason={runtime_state.enqueue_reason or '(none)'}",
        f"last_enqueued_at={format_optional_datetime_for_ui(runtime_state.last_enqueued_at)}",
        f"last_started_at={format_optional_datetime_for_ui(runtime_state.last_started_at)}",
        f"last_finished_at={format_optional_datetime_for_ui(runtime_state.last_finished_at)}",
        f"scan_guard_count={runtime_state.scan_guard_count}",
        "last_skip_reason="
        + (
            format_runtime_skip_message(runtime_state.last_skip_reason)
            if runtime_state.last_skip_reason
            else "(none)"
        ),
        _format_outbox_summary_line(notification_outbox_summary),
        f"scan_status={scan.status.value}",
        f"failure_reason={format_scan_failure_reason(str(metadata.get('reason') or ''))}"
        if scan.status == ScanStatus.FAILED
        else "",
        f"retryable={metadata.get('retryable', '(unknown)')}"
        if scan.status == ScanStatus.FAILED
        else "",
        f"runtime_action={metadata.get('runtime_action', '(unknown)')}"
        if scan.status == ScanStatus.FAILED
        else "",
        f"retry_streak={metadata.get('retry_streak', '(none)')}"
        if scan.status == ScanStatus.FAILED
        else "",
        f"retry_limit={metadata.get('retry_limit', '(none)')}"
        if scan.status == ScanStatus.FAILED
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
    lines = [line for line in lines if line]
    _append_sort_block(lines, "sort_adjust", metadata.get("sort_adjust"))
    _append_sort_block(lines, "comment_sort", metadata.get("comment_sort"))
    _append_comments_meta(lines, metadata.get("comments_meta"))
    _append_collected_meta(lines, metadata.get("collected_meta"))
    if latest_failed_scan_run:
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
    _append_rounds(lines, "rounds", metadata.get("rounds"), format_scan_round_debug)
    _append_rounds(
        lines,
        "comment_extract_rounds",
        metadata.get("comment_extract_rounds"),
        format_comment_round_debug,
    )
    _append_latest_scan_items(lines, latest_scan_items)
    lines.append("metadata_json=" + json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def _format_outbox_summary_line(summary: NotificationOutboxSummary | None) -> str:
    """格式化 target-scoped outbox backlog 診斷摘要。"""

    if summary is None:
        return "outbox=(unavailable)"
    return (
        "outbox="
        f"pending:{summary.pending_count},"
        f"processing:{summary.processing_count},"
        f"failed:{summary.failed_count},"
        f"terminal:{summary.terminal_count},"
        f"oldest_pending:{format_optional_datetime_for_ui(summary.oldest_pending_updated_at)},"
        f"max_attempts:{summary.max_attempts}"
    )


def _append_latest_scan_items(lines: list[str], items: tuple[LatestScanItem, ...]) -> None:
    """附加最近掃描每筆 item 的除錯資訊。"""

    if not items:
        return
    lines.extend(["", "latest_scan_items:"])
    for item in items:
        lines.extend(_format_latest_scan_item_debug_lines(item))


def _format_latest_scan_item_debug_lines(item: LatestScanItem) -> list[str]:
    """把單筆 latest scan item metadata 轉成掃描診斷文字。"""

    metadata = item.debug_metadata or {}
    lines = [
        f"- item_key={item.item_key}",
        f"  item_kind={item.item_kind.value}",
        f"  index={item.item_index}",
        f"  author={item.author or '(unknown)'}",
        f"  permalink={item.permalink or '(none)'}",
        f"  matched_keyword={item.matched_keyword or '(none)'}",
        f"  text={_format_latest_item_text(item.display_text or item.text)}",
    ]
    for key in _LATEST_SCAN_ITEM_DEBUG_KEYS:
        if key in metadata:
            lines.append(f"  {key}={_format_debug_value(metadata[key])}")
    return lines


def _format_latest_item_text(text: str) -> str:
    """整理單筆 item 文字，避免診斷輸出被換行切斷。"""

    preview = " ".join(text.split())
    return preview or "(empty)"


def _format_debug_value(value: object) -> str:
    """將巢狀 debug 值轉成穩定 JSON，方便複製給 review。"""

    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _is_empty_diagnostic_value(value: object) -> bool:
    """判斷 sort diagnostics 欄位是否應從日常輸出省略。"""

    return value is None or value == "" or value == [] or value == {}


def _append_sort_block(lines: list[str], label: str, value: object) -> None:
    """附加 feed/comment sort diagnostics。"""

    if not isinstance(value, dict):
        return
    lines.extend(
        [
            "",
            f"{label}:",
            f"attempted={value.get('attempted', False)}",
            f"changed={value.get('changed', False)}",
            f"preferred_label={value.get('preferred_label', '')}",
            f"before_label={value.get('before_label', '')}",
            f"after_label={value.get('after_label', '')}",
            f"reason={value.get('reason', '')}",
            f"mutation_suppression_ms={value.get('mutation_suppression_ms', 0)}",
            f"mutation_suppression_reason={value.get('mutation_suppression_reason', '')}",
        ]
    )
    menu_candidate_texts = value.get("menu_candidate_texts")
    if menu_candidate_texts:
        lines.append(f"menu_candidate_texts={_format_debug_value(menu_candidate_texts)}")
    for key in _SORT_DIAGNOSTIC_KEYS:
        if key not in value:
            continue
        if _is_empty_diagnostic_value(value[key]):
            continue
        lines.append(f"{key}={_format_debug_value(value[key])}")


def _append_comments_meta(lines: list[str], value: object) -> None:
    """附加 comments extractor aggregate diagnostics。"""

    if not isinstance(value, dict):
        return
    lines.extend(
        [
            "",
            "comments_meta:",
            f"mode={value.get('mode', '')}",
            f"targetCount={value.get('targetCount', 0)}",
            f"attempted={value.get('attempted', False)}",
            f"attempts={value.get('attempts', 0)}",
            f"beforeCount={value.get('beforeCount', 0)}",
            f"afterCount={value.get('afterCount', 0)}",
            f"windowCount={value.get('windowCount', 0)}",
            f"candidateCount={value.get('candidateCount', 0)}",
            f"parsedCount={value.get('parsedCount', 0)}",
            f"accumulatedCount={value.get('accumulatedCount', 0)}",
            f"maxWindowCount={value.get('maxWindowCount', 0)}",
            f"stagnantWindows={value.get('stagnantWindows', 0)}",
            f"loadMoreMode={value.get('loadMoreMode', '')}",
            f"guardReason={value.get('guardReason', '')}",
            f"filteredEmptyTextCount={value.get('filteredEmptyTextCount', 0)}",
            f"filteredNonPostCount={value.get('filteredNonPostCount', 0)}",
            f"articleElementCount={value.get('articleElementCount', 0)}",
            f"commentsWithCommentIdCount={value.get('commentsWithCommentIdCount', 0)}",
            f"stopReason={format_scan_stop_reason(str(value.get('stopReason') or ''))}",
        ]
    )


def _append_collected_meta(lines: list[str], value: object) -> None:
    """附加 posts extractor aggregate diagnostics。"""

    if not isinstance(value, dict):
        return
    lines.extend(
        [
            "",
            "collected_meta:",
            f"mode={value.get('mode', '')}",
            f"attempted={value.get('attempted', False)}",
            f"attempts={value.get('attempts', 0)}",
            f"beforeCount={value.get('beforeCount', 0)}",
            f"afterCount={value.get('afterCount', 0)}",
            f"windowCount={value.get('windowCount', 0)}",
            f"candidateCount={value.get('candidateCount', 0)}",
            f"parsedCount={value.get('parsedCount', 0)}",
            f"accumulatedCount={value.get('accumulatedCount', 0)}",
            f"maxWindowCount={value.get('maxWindowCount', 0)}",
            f"stagnantWindows={value.get('stagnantWindows', 0)}",
            f"loadMoreMode={value.get('loadMoreMode', '')}",
            f"stopReason={format_scan_stop_reason(str(value.get('stopReason') or ''))}",
            f"filteredEmptyTextCount={value.get('filteredEmptyTextCount', 0)}",
            f"filteredNonPostCount={value.get('filteredNonPostCount', 0)}",
            f"filteredFeedSortControlCount={value.get('filteredFeedSortControlCount', 0)}",
            f"articleElementCount={value.get('articleElementCount', 0)}",
            f"postsWithPostIdCount={value.get('postsWithPostIdCount', 0)}",
        ]
    )


def _append_rounds(
    lines: list[str],
    label: str,
    value: object,
    formatter: Callable[[dict[str, Any]], str],
) -> None:
    """附加每輪 extractor diagnostics。"""

    if not isinstance(value, list) or not value:
        return
    lines.extend(["", f"{label}:"])
    for round_item in value:
        if isinstance(round_item, dict):
            lines.append(formatter(round_item))


def format_datetime_for_ui(value: datetime) -> str:
    """將時間轉成本機時區的短格式供 UI 顯示。"""

    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_optional_datetime_for_ui(value: datetime | None) -> str:
    """將可為空的時間轉成 UI 診斷文字。"""

    if value is None:
        return "(none)"
    return format_datetime_for_ui(value)


def format_scan_stop_reason(value: str) -> str:
    """把 worker 內部停止原因轉成 UI 可讀文字。"""

    labels = {
        "target_count_reached": "達到目標筆數",
        "seen_stop_consecutive_seen": "前段內容重複，略過深度掃描",
        "scroll_rounds_completed": "完成捲動輪數",
        "scroll_stalled": "頁面未產生可用捲動",
        "stagnant_windows": "連續多輪沒有新增項目",
        "collection_stopped": "抽取流程停止",
        "no_round_stats": "無輪次資料",
        "visible_window_completed": "完成可見留言抽取",
        "auto_load_more_disabled": "已停用自動載入更多",
        "no_comment_round_stats": "無留言輪次資料",
        "comment_scroll_stalled": "留言區未產生可用捲動",
        "comment_stagnant_windows": "留言連續多輪沒有新增項目",
        "comment_scroll_rounds_completed": "完成留言捲動輪數",
        "comment_collection_stopped": "留言抽取流程停止",
        "comment_load_more_guard_active": "留言載入更多 guard 使用中",
        "sort_adjust_unconfirmed_skip": "調整排序失敗，已跳過掃描",
    }
    return labels.get(value, value or "(未知)")


def format_scan_failure_reason(value: str) -> str:
    """把 failed scan reason 轉成 UI 可讀文字。"""

    return _format_failure_reason(value)


def format_scan_cycle_result_reason(value: str) -> str:
    """把最近一輪停止原因轉成 target card 可讀的低干擾文案。"""

    labels = {
        "target_count_reached": "已達目標項目數",
        "seen_stop_consecutive_seen": "前段內容重複，略過深度掃描",
        "scroll_rounds_completed": "已完成深度掃描",
        "scroll_stalled": "頁面無法繼續捲動",
        "stagnant_windows": "多輪未找到新項目",
        "collection_stopped": "抽取流程結束",
        "no_round_stats": "沒有掃描輪次資料",
        "visible_window_completed": "已完成可見留言掃描",
        "auto_load_more_disabled": "未啟用深度掃描",
        "no_comment_round_stats": "沒有留言掃描輪次資料",
        "comment_scroll_stalled": "留言區無法繼續捲動",
        "comment_stagnant_windows": "多輪未找到新留言",
        "comment_scroll_rounds_completed": "已完成留言深度掃描",
        "comment_collection_stopped": "留言抽取流程結束",
        "comment_load_more_guard_active": "留言載入更多正在使用中",
        "sort_adjust_unconfirmed_skip": "調整排序失敗，已跳過掃描",
    }
    return labels.get(value, value or "未知原因")


def format_scan_round_debug(round_item: dict[str, Any]) -> str:
    """格式化單輪 posts extractor 診斷資料。"""

    return (
        f"- round={round_item.get('round_index', '(unknown)')} "
        f"raw={round_item.get('raw_item_count', '(unknown)')} "
        f"unique={round_item.get('unique_item_count', '(unknown)')} "
        f"scroll_y={round_item.get('scroll_y', '(unknown)')} "
        f"scroll_height={round_item.get('scroll_height', '(unknown)')}"
        + (
            f" target={round_item.get('scroll_target_label')} "
            f"moved={round_item.get('scroll_moved')}"
            f" added={round_item.get('added_count', '(unknown)')}"
            f" stagnant={round_item.get('stagnant_windows', '(unknown)')}"
            if round_item.get("scroll_target_label")
            else ""
        )
    )


def format_comment_round_debug(round_item: dict[str, Any]) -> str:
    """格式化 comments 單輪 extractor / scroll 診斷資料。"""

    return (
        f"- round={round_item.get('round_index', '(unknown)')} "
        f"raw={round_item.get('raw_item_count', '(unknown)')} "
        f"unique={round_item.get('unique_item_count', '(unknown)')} "
        f"candidate={round_item.get('candidate_count', '(unknown)')} "
        f"parsed={round_item.get('parsed_count', '(unknown)')} "
        f"target={round_item.get('scroll_target_label', '') or '(none)'} "
        f"moved={round_item.get('scroll_moved')} "
        f"added={round_item.get('added_count', '(unknown)')} "
        f"stagnant={round_item.get('stagnant_windows', '(unknown)')}"
    )
