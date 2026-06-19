"""Facebook comments extraction payload normalizers。

職責：將 comments DOM extractor 回傳的 payload 轉成 ExtractedItem 與
CommentCollectionMeta，並保留 comment aliases 去重規則。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.facebook.comment_extraction_diagnostics import CommentCollectionMeta
from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.extracted_item import make_item_key_aliases
from facebook_monitor.facebook.text_cleanup import clean_facebook_multiline_text
from facebook_monitor.facebook.text_cleanup import clean_facebook_text


def normalize_comment_extraction_payload(
    raw_payload: object,
    *,
    max_items: int,
) -> tuple[list[ExtractedItem], CommentCollectionMeta]:
    """整理 DOM extractor payload，並用 comment aliases 去重。"""

    raw_meta, raw_items = _split_comment_payload(raw_payload)
    items = _unique_normalized_comment_items(raw_items, max_items=max_items)
    return items, _comment_payload_meta(
        raw_meta,
        raw_items=raw_items,
        items=items,
        max_items=max_items,
    )


def _split_comment_payload(raw_payload: object) -> tuple[dict[str, Any], list[object]]:
    """拆出 DOM extractor payload 的 meta 與 items。"""

    if not isinstance(raw_payload, Mapping):
        return {}, raw_payload if isinstance(raw_payload, list) else []
    raw_items = raw_payload.get("items") or []
    return (
        dict(raw_payload.get("meta") or {}),
        raw_items if isinstance(raw_items, list) else [],
    )


def _unique_normalized_comment_items(
    raw_items: list[object],
    *,
    max_items: int,
) -> list[ExtractedItem]:
    """將 raw comments 轉成 ExtractedItem 並依 aliases 去重。"""

    collected: list[tuple[tuple[str, ...], ExtractedItem]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        item = _normalized_comment_item(raw_item)
        aliases = make_item_key_aliases(item)
        if not aliases:
            continue
        if any(aliases_overlap(aliases, existing_aliases) for existing_aliases, _ in collected):
            continue
        collected.append((aliases, item))
        if len(collected) >= max(max_items, 1):
            break
    return [item for _aliases, item in collected]


def _normalized_comment_item(raw_item: Mapping[str, Any]) -> ExtractedItem:
    """將單筆 comment DOM payload 轉成穩定 item model。"""

    cleaned_text = clean_facebook_text(raw_item.get("text") or "")
    display_text = clean_facebook_multiline_text(
        raw_item.get("displayText") or cleaned_text
    ) or cleaned_text
    return ExtractedItem(
        text=cleaned_text,
        text_length=len(cleaned_text),
        permalink=str(raw_item.get("permalink") or ""),
        link_count=int(raw_item.get("linkCount") or 0),
        display_text=display_text,
        author=str(raw_item.get("author") or ""),
        debug_metadata=normalize_comment_debug_metadata(raw_item),
        item_kind="comment",
        parent_post_id=str(raw_item.get("parentPostId") or ""),
        comment_id=str(raw_item.get("commentId") or ""),
    )


def _comment_payload_meta(
    raw_meta: Mapping[str, Any],
    *,
    raw_items: list[object],
    items: list[ExtractedItem],
    max_items: int,
) -> CommentCollectionMeta:
    """建立單次 visible-window extractor meta。"""

    return CommentCollectionMeta(
        target_count=max(max_items, 1),
        candidate_count=int(raw_meta.get("candidateCount") or len(raw_items)),
        parsed_count=int(raw_meta.get("parsedCount") or len(items)),
        accumulated_count=len(items),
        filtered_empty_text_count=int(raw_meta.get("filteredEmptyTextCount") or 0),
        filtered_non_post_count=int(raw_meta.get("filteredNonPostCount") or 0),
        article_element_count=int(raw_meta.get("articleElementCount") or 0),
        comments_with_comment_id_count=int(raw_meta.get("commentsWithCommentIdCount") or 0),
        filtered_out_of_scope_count=int(raw_meta.get("filteredOutOfScopeCount") or 0),
        comment_search_root_strategy=str(raw_meta.get("commentSearchRootStrategy") or ""),
        current_route_post_id=str(raw_meta.get("currentRoutePostId") or ""),
        current_route_matches_target=bool(raw_meta.get("currentRouteMatchesTarget")),
        stop_reason=str(raw_meta.get("stopReason") or "visible_window_completed"),
    )


def normalize_comment_debug_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    """整理 comment DOM extractor 回傳的診斷欄位。"""

    keys = (
        "source",
        "containerRole",
        "textSource",
        "textDiagnostics",
        "textLength",
        "displayTextLength",
        "rawTextLength",
        "rawDisplayTextLength",
        "permalinkSource",
        "canonicalPermalinkCandidateCount",
        "parentPostId",
        "commentId",
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
        "author",
        "groupId",
    )
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "")}


__all__ = [
    "normalize_comment_debug_metadata",
    "normalize_comment_extraction_payload",
]
