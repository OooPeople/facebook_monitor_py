"""Scan diagnostics comments extractor section formatter。"""

from __future__ import annotations

from typing import Any

from facebook_monitor.webapp.scan_reason_presenters import format_scan_stop_reason


def append_comments_meta(lines: list[str], value: object) -> None:
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
