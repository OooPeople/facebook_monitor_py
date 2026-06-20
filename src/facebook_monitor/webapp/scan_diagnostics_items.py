"""Latest scan item diagnostics formatter。"""

from __future__ import annotations

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.webapp.scan_diagnostics_values import format_diagnostic_value


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


def append_latest_scan_items(
    lines: list[str],
    items: tuple[LatestScanItem, ...],
) -> None:
    """附加最近掃描每筆 item 的除錯資訊。"""

    if not items:
        return
    lines.extend(["", "latest_scan_items:"])
    for item in items:
        lines.extend(format_latest_scan_item_debug_lines(item))


def format_latest_scan_item_debug_lines(item: LatestScanItem) -> list[str]:
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
            lines.append(f"  {key}={format_diagnostic_value(metadata[key])}")
    return lines


def _format_latest_item_text(text: str) -> str:
    """整理單筆 item 文字，避免診斷輸出被換行切斷。"""

    preview = " ".join(text.split())
    return preview or "(empty)"
