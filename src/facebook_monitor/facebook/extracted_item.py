"""Shared extracted scan item identity helpers。

職責：保存 posts/comments extractor 共用的原始 item 形狀與 dedupe identity
轉換，不隸屬於 feed extractor。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from facebook_monitor.core.dedupe import ScanItemIdentity
from facebook_monitor.core.dedupe import get_item_key_aliases
from facebook_monitor.core.dedupe import get_primary_item_key


@dataclass(frozen=True)
class ExtractedItem:
    """保存單筆 scan 候選的最小資料，不負責保存原文到 log。"""

    text: str
    text_length: int
    permalink: str
    link_count: int
    display_text: str = ""
    author: str = ""
    debug_metadata: dict[str, Any] | None = None
    item_kind: str = "post"
    parent_post_id: str = ""
    comment_id: str = ""


def make_item_key(item: ExtractedItem) -> str:
    """依 item key aliases 產生主要 dedupe key。"""

    return get_primary_item_key(build_scan_item_identity(item))


def make_item_key_aliases(item: ExtractedItem) -> tuple[str, ...]:
    """回傳同一 scan item 可接受的多組 dedupe aliases。"""

    return get_item_key_aliases(build_scan_item_identity(item))


def build_scan_item_identity(item: ExtractedItem) -> ScanItemIdentity:
    """將 extractor item 轉成核心 dedupe identity。"""

    return ScanItemIdentity(
        text=item.text,
        permalink=item.permalink,
        author=item.author,
        item_kind=item.item_kind,
        parent_post_id=item.parent_post_id,
        comment_id=item.comment_id,
    )
