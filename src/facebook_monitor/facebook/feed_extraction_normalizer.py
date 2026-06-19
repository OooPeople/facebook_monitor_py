"""Facebook feed extraction payload normalizers。

職責：將 feed DOM script 回傳的 raw payload 轉成穩定 ExtractedItem 與
privacy-safe debug metadata；不負責捲動、seen-stop 或 collection loop。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from facebook_monitor.facebook.extracted_item import ExtractedItem
from facebook_monitor.facebook.text_cleanup import clean_facebook_multiline_text


def normalize_feed_extraction_payload(
    raw_payload: object,
) -> tuple[list[ExtractedItem], dict[str, Any]]:
    """整理 feed DOM extractor payload，並保留 DOM 層 collected meta。"""

    raw_meta: dict[str, Any] = {}
    raw_items: object = raw_payload
    if isinstance(raw_payload, Mapping):
        raw_meta = dict(raw_payload.get("meta") or {})
        raw_items = raw_payload.get("items") or []
    if not isinstance(raw_items, list):
        raw_items = []
    return [
        normalize_feed_extraction_item(item)
        for item in raw_items
        if isinstance(item, Mapping)
    ], raw_meta


def normalize_feed_extraction_item(item: Mapping[str, Any]) -> ExtractedItem:
    """將單一 feed DOM item 轉成 extractor 共用模型。"""

    text = str(item.get("text") or "")
    display_text = clean_facebook_multiline_text(
        item.get("displayText") or text
    ) or text
    return ExtractedItem(
        text=text,
        text_length=int(item.get("textLength") or 0),
        permalink=str(item.get("permalink") or ""),
        link_count=int(item.get("linkCount") or 0),
        display_text=display_text,
        author=str(item.get("author") or ""),
        debug_metadata=normalize_debug_metadata(item),
    )


def normalize_debug_metadata(item: Any) -> dict[str, Any]:
    """整理 DOM extractor 回傳的診斷欄位，避免保存過大的任意 payload。"""

    if not isinstance(item, Mapping):
        return {}
    keys = (
        "source",
        "containerRole",
        "firstSeenRound",
        "roundItemIndex",
        "collectionIndex",
        "domIndex",
        "domPosition",
        "textSource",
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
        "linkCount",
        "linkDiagnostics",
        "author",
        "hasStoryMessage",
        "hasCommentPermalink",
        "warmupAttempted",
        "warmupResolved",
        "warmupCandidateCount",
        "warmupDiagnostics",
        "expandAttempted",
        "expandCount",
    )
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "")}


__all__ = [
    "normalize_debug_metadata",
    "normalize_feed_extraction_item",
    "normalize_feed_extraction_payload",
]
