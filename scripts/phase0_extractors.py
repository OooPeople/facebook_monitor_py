"""Phase 0 Facebook feed extractor helper。

職責：保留 Phase 0 script 的既有 import 入口，實作已移到正式 package。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.facebook.feed_extractor import ExtractedItem
from facebook_monitor.facebook.feed_extractor import ExtractRoundStats
from facebook_monitor.facebook.feed_extractor import collect_items_with_diagnostics
from facebook_monitor.facebook.feed_extractor import count_matches
from facebook_monitor.facebook.feed_extractor import extract_post_like_items
from facebook_monitor.facebook.feed_extractor import find_first_matching_keyword
from facebook_monitor.facebook.feed_extractor import get_scroll_position
from facebook_monitor.facebook.feed_extractor import make_item_key
from facebook_monitor.facebook.feed_extractor import normalize_text_fingerprint
from facebook_monitor.facebook.permalink import normalize_permalink

__all__ = [
    "ExtractedItem",
    "ExtractRoundStats",
    "collect_items_with_diagnostics",
    "count_matches",
    "extract_post_like_items",
    "find_first_matching_keyword",
    "get_scroll_position",
    "is_post_permalink",
    "make_item_key",
    "normalize_permalink",
    "normalize_text_fingerprint",
]


def is_post_permalink(raw_url: str) -> bool:
    """Phase 0 相容入口：判斷 URL 是否可正規化為 Facebook 貼文 permalink。"""

    return bool(normalize_permalink(raw_url))
