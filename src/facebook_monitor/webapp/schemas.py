"""Web UI view models。

職責：把 domain model 與 config 整理成 template 容易使用的資料結構。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import is_generated_group_comments_name
from facebook_monitor.core.models import is_generated_group_posts_name
from facebook_monitor.facebook.route_detection import clean_facebook_page_title


@dataclass(frozen=True)
class LatestScanItemRow:
    """保存 target 卡片右側最近掃描項目顯示資料。"""

    item: LatestScanItem

    @property
    def author_label(self) -> str:
        """回傳作者顯示文字。"""

        return self.item.author or "(unknown)"

    @property
    def match_label(self) -> str:
        """回傳掃描項目是否命中 keyword 的顯示文字。"""

        return f"命中: {self.item.matched_keyword}" if self.item.matched_keyword else "未命中"

    @property
    def preview_text(self) -> str:
        """回傳掃描項目內容預覽。"""

        preview = " ".join(self.item.text.split())
        if len(preview) > 120:
            return preview[:117] + "..."
        return preview

    @property
    def debug_text(self) -> str:
        """回傳可複製給開發者定位 extractor 問題的除錯文字。"""

        metadata = self.item.debug_metadata or {}
        lines = [
            f"item_key={self.item.item_key}",
            f"item_kind={self.item.item_kind.value}",
            f"index={self.item.item_index}",
            f"author={self.item.author or '(unknown)'}",
            f"permalink={self.item.permalink or '(none)'}",
            f"matched_keyword={self.item.matched_keyword or '(none)'}",
            f"text={self.preview_text or '(empty)'}",
        ]
        for key in (
            "source",
            "containerRole",
            "textSource",
            "textLength",
            "rawTextLength",
            "permalinkSource",
            "canonicalPermalinkCandidateCount",
            "postId",
            "postIdSource",
            "parentPostId",
            "commentId",
            "linkCount",
            "hasStoryMessage",
            "hasCommentPermalink",
            "warmupAttempted",
            "warmupResolved",
            "warmupCandidateCount",
            "expandAttempted",
            "expandCount",
        ):
            if key in metadata:
                lines.append(f"{key}={metadata[key]}")
        if metadata:
            lines.append("debug_json=" + json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        return "\n".join(lines)

    @property
    def debug_summary(self) -> str:
        """回傳除錯摘要，方便 UI 快速掃描。"""

        metadata = self.item.debug_metadata or {}
        if self.item.item_kind.value == "comment":
            return (
                f"文字={metadata.get('textSource', '(無)')} · "
                f"連結={metadata.get('permalinkSource', '(無)')} · "
                f"commentId={metadata.get('commentId', '(無)')}"
            )
        return (
            f"文字={metadata.get('textSource', '(無)')} · "
            f"連結={metadata.get('permalinkSource', '(無)')} · "
            f"展開={metadata.get('expandCount', 0)} · "
            f"warmup={metadata.get('warmupAttempted', False)}"
        )


@dataclass(frozen=True)
class TargetRow:
    """保存 target 清單顯示所需資料。"""

    target: TargetDescriptor
    config: TargetConfig
    runtime_state: TargetRuntimeState
    latest_scan_run: ScanRun | None = None
    latest_failed_scan_run: ScanRun | None = None
    latest_notification_event: NotificationEvent | None = None
    latest_scan_items: tuple[LatestScanItemRow, ...] = ()

    @property
    def latest_items_heading(self) -> str:
        """回傳右側最近掃描項目的標題。"""

        label = "留言" if self.target.target_kind == TargetKind.COMMENTS else "貼文"
        return f"最近掃描{label}（{len(self.latest_scan_items)}）"

    @property
    def latest_item_link_label(self) -> str:
        """回傳右側項目 permalink 的連結文字。"""

        return "開啟留言" if self.target.target_kind == TargetKind.COMMENTS else "開啟貼文"

    @property
    def display_name(self) -> str:
        """回傳 UI 顯示名稱。"""

        if self.target.target_kind == TargetKind.COMMENTS:
            if self.target.name and not is_generated_group_comments_name(
                self.target.name,
                self.target.group_id,
                self.target.parent_post_id,
            ):
                return clean_facebook_page_title(self.target.name)
            base_name = self.target.group_name or self.target.name
            return clean_facebook_page_title(base_name)
        if self.target.name and not is_generated_group_posts_name(
            self.target.name,
            self.target.group_id,
        ):
            return clean_facebook_page_title(self.target.name)
        return clean_facebook_page_title(self.target.group_name or self.target.name)

    @property
    def kind_label(self) -> str:
        """回傳 target 類型顯示文字。"""

        return "comments" if self.target.target_kind == TargetKind.COMMENTS else "posts"

    @property
    def scanning_supported(self) -> bool:
        """回傳目前 target 是否已接上 worker 掃描流程。"""

        return self.target.target_kind in {TargetKind.POSTS, TargetKind.COMMENTS}

    @property
    def target_identity_label(self) -> str:
        """回傳 target 的 group/post/scope 診斷摘要。"""

        if self.target.target_kind == TargetKind.COMMENTS:
            return (
                f"group={self.target.group_id} · "
                f"parent_post={self.target.parent_post_id or '(none)'} · "
                f"scope={self.target.scope_id}"
            )
        return f"group={self.target.group_id} · scope={self.target.scope_id}"

    @property
    def status_label(self) -> str:
        """回傳 target 啟停狀態文字。"""

        if not self.target.enabled:
            return "停用"
        if self.target.paused:
            return "已停止"
        if not self.scanning_supported:
            return "尚未接上掃描"
        return f"已啟用 · {self.runtime_state.runtime_status.value}"

    @property
    def status_class(self) -> str:
        """回傳 target 狀態對應 CSS class。"""

        if not self.target.enabled:
            return "muted"
        if self.target.paused:
            return "stopped"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.QUEUED:
            return "queued"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.RUNNING:
            return "running"
        if self.runtime_state.runtime_status == TargetRuntimeStatus.ERROR:
            return "error"
        return "enabled"

    @property
    def runtime_error(self) -> str:
        """回傳 runtime error 顯示文字。"""

        if self.runtime_state.runtime_status != TargetRuntimeStatus.ERROR:
            return ""
        return self.runtime_state.last_error

    @property
    def runtime_skip_reason(self) -> str:
        """回傳最近一次 scan guard skip 原因。"""

        return self.runtime_state.last_skip_reason

    @property
    def latest_scan_label(self) -> str:
        """回傳最近掃描完成時間。"""

        if not self.latest_scan_run:
            return "尚無掃描"
        return format_datetime_for_ui(self.latest_scan_run.finished_at)

    @property
    def latest_scan_summary(self) -> str:
        """回傳最近成功掃描的數量摘要。"""

        if not self.latest_scan_run:
            return "尚無掃描摘要"
        metadata = self.latest_scan_run.metadata or {}
        new_count = metadata.get("new_count", "(未知)")
        return (
            f"items={self.latest_scan_run.item_count} · "
            f"new={new_count} · "
            f"matched={self.latest_scan_run.matched_count}"
        )

    @property
    def latest_scan_diagnostics_summary(self) -> str:
        """回傳最近成功掃描的診斷短摘要。"""

        if not self.latest_scan_run:
            return "尚無掃描診斷"
        metadata = self.latest_scan_run.metadata or {}
        round_count = metadata.get("round_count", 0)
        candidate_count = metadata.get("candidate_count", self.latest_scan_run.item_count)
        stop_reason = format_scan_stop_reason(str(metadata.get("stop_reason") or ""))
        return f"rounds={round_count} · candidates={candidate_count} · stop={stop_reason}"

    @property
    def latest_scan_diagnostics_text(self) -> str:
        """回傳可複製的 scan-level diagnostics。"""

        if not self.latest_scan_run:
            return "\n".join(
                [
                    f"target_id={self.target.id}",
                    f"target_kind={self.target.target_kind.value}",
                    f"group_id={self.target.group_id}",
                    f"parent_post_id={self.target.parent_post_id or '(none)'}",
                    f"scope_id={self.target.scope_id}",
                    f"runtime_status={self.runtime_state.runtime_status.value}",
                    "scan_status=(none)",
                    "note=尚無掃描診斷",
                ]
            )
        scan = self.latest_scan_run
        metadata = scan.metadata or {}
        lines = [
            f"target_id={self.target.id}",
            f"target_kind={self.target.target_kind.value}",
            f"group_id={self.target.group_id}",
            f"parent_post_id={self.target.parent_post_id or '(none)'}",
            f"scope_id={self.target.scope_id}",
            f"runtime_status={self.runtime_state.runtime_status.value}",
            f"queued={self.runtime_state.queued}",
            f"running={self.runtime_state.running}",
            f"active_worker_id={self.runtime_state.active_worker_id or '(none)'}",
            f"active_page_id={self.runtime_state.active_page_id or '(none)'}",
            f"last_page_reloaded_at={format_optional_datetime_for_ui(self.runtime_state.last_page_reloaded_at)}",
            f"enqueue_reason={self.runtime_state.enqueue_reason or '(none)'}",
            f"last_enqueued_at={format_optional_datetime_for_ui(self.runtime_state.last_enqueued_at)}",
            f"last_started_at={format_optional_datetime_for_ui(self.runtime_state.last_started_at)}",
            f"last_finished_at={format_optional_datetime_for_ui(self.runtime_state.last_finished_at)}",
            f"scan_guard_count={self.runtime_state.scan_guard_count}",
            f"last_skip_reason={self.runtime_state.last_skip_reason or '(none)'}",
            f"scan_status={scan.status.value}",
            f"finished_at={format_datetime_for_ui(scan.finished_at)}",
            f"item_count={scan.item_count}",
            f"matched_count={scan.matched_count}",
            f"new_count={metadata.get('new_count', '(unknown)')}",
            f"target_count={metadata.get('target_count', self.config.max_items_per_scan)}",
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
        sort_adjust = metadata.get("sort_adjust")
        if isinstance(sort_adjust, dict):
            lines.extend(
                [
                    "",
                    "sort_adjust:",
                    f"attempted={sort_adjust.get('attempted', False)}",
                    f"changed={sort_adjust.get('changed', False)}",
                    f"preferred_label={sort_adjust.get('preferred_label', '')}",
                    f"before_label={sort_adjust.get('before_label', '')}",
                    f"after_label={sort_adjust.get('after_label', '')}",
                    f"reason={sort_adjust.get('reason', '')}",
                    f"mutation_suppression_ms={sort_adjust.get('mutation_suppression_ms', 0)}",
                    f"mutation_suppression_reason={sort_adjust.get('mutation_suppression_reason', '')}",
                ]
            )
        comment_sort = metadata.get("comment_sort")
        if isinstance(comment_sort, dict):
            lines.extend(
                [
                    "",
                    "comment_sort:",
                    f"attempted={comment_sort.get('attempted', False)}",
                    f"changed={comment_sort.get('changed', False)}",
                    f"preferred_label={comment_sort.get('preferred_label', '')}",
                    f"before_label={comment_sort.get('before_label', '')}",
                    f"after_label={comment_sort.get('after_label', '')}",
                    f"reason={comment_sort.get('reason', '')}",
                    f"mutation_suppression_ms={comment_sort.get('mutation_suppression_ms', 0)}",
                    f"mutation_suppression_reason={comment_sort.get('mutation_suppression_reason', '')}",
                ]
            )
        comments_meta = metadata.get("comments_meta")
        if isinstance(comments_meta, dict):
            lines.extend(
                [
                    "",
                    "comments_meta:",
                    f"mode={comments_meta.get('mode', '')}",
                    f"targetCount={comments_meta.get('targetCount', 0)}",
                    f"attempted={comments_meta.get('attempted', False)}",
                    f"attempts={comments_meta.get('attempts', 0)}",
                    f"beforeCount={comments_meta.get('beforeCount', 0)}",
                    f"afterCount={comments_meta.get('afterCount', 0)}",
                    f"windowCount={comments_meta.get('windowCount', 0)}",
                    f"candidateCount={comments_meta.get('candidateCount', 0)}",
                    f"parsedCount={comments_meta.get('parsedCount', 0)}",
                    f"accumulatedCount={comments_meta.get('accumulatedCount', 0)}",
                    f"maxWindowCount={comments_meta.get('maxWindowCount', 0)}",
                    f"stagnantWindows={comments_meta.get('stagnantWindows', 0)}",
                    f"loadMoreMode={comments_meta.get('loadMoreMode', '')}",
                    f"guardReason={comments_meta.get('guardReason', '')}",
                    f"filteredEmptyTextCount={comments_meta.get('filteredEmptyTextCount', 0)}",
                    f"filteredNonPostCount={comments_meta.get('filteredNonPostCount', 0)}",
                    f"articleElementCount={comments_meta.get('articleElementCount', 0)}",
                    f"commentsWithCommentIdCount={comments_meta.get('commentsWithCommentIdCount', 0)}",
                    f"stopReason={format_scan_stop_reason(str(comments_meta.get('stopReason') or ''))}",
                ]
            )
        collected_meta = metadata.get("collected_meta")
        if isinstance(collected_meta, dict):
            lines.extend(
                [
                    "",
                    "collected_meta:",
                    f"mode={collected_meta.get('mode', '')}",
                    f"attempted={collected_meta.get('attempted', False)}",
                    f"attempts={collected_meta.get('attempts', 0)}",
                    f"beforeCount={collected_meta.get('beforeCount', 0)}",
                    f"afterCount={collected_meta.get('afterCount', 0)}",
                    f"windowCount={collected_meta.get('windowCount', 0)}",
                    f"candidateCount={collected_meta.get('candidateCount', 0)}",
                    f"parsedCount={collected_meta.get('parsedCount', 0)}",
                    f"accumulatedCount={collected_meta.get('accumulatedCount', 0)}",
                    f"maxWindowCount={collected_meta.get('maxWindowCount', 0)}",
                    f"stagnantWindows={collected_meta.get('stagnantWindows', 0)}",
                    f"loadMoreMode={collected_meta.get('loadMoreMode', '')}",
                    f"stopReason={format_scan_stop_reason(str(collected_meta.get('stopReason') or ''))}",
                    f"filteredEmptyTextCount={collected_meta.get('filteredEmptyTextCount', 0)}",
                    f"filteredNonPostCount={collected_meta.get('filteredNonPostCount', 0)}",
                    f"filteredFeedSortControlCount={collected_meta.get('filteredFeedSortControlCount', 0)}",
                    f"articleElementCount={collected_meta.get('articleElementCount', 0)}",
                    f"postsWithPostIdCount={collected_meta.get('postsWithPostIdCount', 0)}",
                ]
            )
        failed = self.latest_failed_scan_run
        if failed:
            lines.extend(
                [
                    "",
                    "latest_failed_scan:",
                    f"finished_at={format_datetime_for_ui(failed.finished_at)}",
                    f"error={failed.error_message or '(none)'}",
                ]
            )
        rounds = metadata.get("rounds")
        if isinstance(rounds, list) and rounds:
            lines.extend(["", "rounds:"])
            for round_item in rounds:
                if not isinstance(round_item, dict):
                    continue
                lines.append(format_scan_round_debug(round_item))
        comment_rounds = metadata.get("comment_extract_rounds")
        if isinstance(comment_rounds, list) and comment_rounds:
            lines.extend(["", "comment_extract_rounds:"])
            for round_item in comment_rounds:
                if not isinstance(round_item, dict):
                    continue
                lines.append(format_comment_round_debug(round_item))
        lines.append("metadata_json=" + json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        return "\n".join(lines)

    @property
    def latest_error_label(self) -> str:
        """回傳最近錯誤時間。"""

        if not self.latest_failed_scan_run:
            return ""
        return format_datetime_for_ui(self.latest_failed_scan_run.finished_at)

    @property
    def latest_failed_scan_summary(self) -> str:
        """回傳最近失敗掃描摘要。"""

        if not self.latest_failed_scan_run:
            return ""
        failed = self.latest_failed_scan_run
        return f"{format_datetime_for_ui(failed.finished_at)} · {failed.error_message}"

    @property
    def latest_notification_label(self) -> str:
        """回傳最近通知通道狀態。"""

        if not self.latest_notification_event:
            return "尚無通知"
        event = self.latest_notification_event
        return (
            f"{event.channel.value}: {event.status.value} · "
            f"{format_datetime_for_ui(event.created_at)}"
            + (f" · {event.message}" if event.message else "")
        )

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return ", ".join(self.config.include_keywords)

    @property
    def exclude_text(self) -> str:
        """回傳 exclude keywords 表單文字。"""

        return ", ".join(self.config.exclude_keywords)

    @property
    def fixed_refresh_value(self) -> int:
        """回傳表單使用的固定掃描間隔秒數。"""

        return self.config.fixed_refresh_sec or PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec

    @property
    def monitoring_action(self) -> str:
        """回傳主操作按鈕應提交的 monitoring action。"""

        return "start" if self.target.paused or not self.target.enabled else "stop"

    @property
    def monitoring_button_label(self) -> str:
        """回傳主操作按鈕文字，對齊 userscript 開始 / 暫停語義。"""

        return "開始" if self.monitoring_action == "start" else "停止"


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
        "scroll_rounds_completed": "完成捲動輪數",
        "scroll_stalled": "頁面未產生可用捲動",
        "stagnant_windows": "連續多輪沒有新增項目",
        "collection_stopped": "抽取流程停止",
        "no_round_stats": "無輪次資料",
        "visible_window_completed": "完成可見留言抽取",
        "auto_load_more_disabled": "已停用自動載入更多",
        "comment_scroll_stalled": "留言區未產生可用捲動",
        "comment_stagnant_windows": "留言連續多輪沒有新增項目",
        "comment_scroll_rounds_completed": "完成留言捲動輪數",
        "comment_collection_stopped": "留言抽取流程停止",
        "comment_load_more_guard_active": "留言載入更多 guard 使用中",
    }
    return labels.get(value, value or "(未知)")


def format_scan_round_debug(round_item: dict[str, Any]) -> str:
    """格式化單輪 extractor 診斷資料。"""

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
