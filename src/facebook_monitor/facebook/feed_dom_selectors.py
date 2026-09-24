"""Facebook feed DOM selector 單一來源。

職責：集中正式 feed extractor 與 page guard 共用的 root、貼文候選與 permalink
selector，避免兩套 browser-side script 各自漂移。
"""

from __future__ import annotations

import json


POST_PERMALINK_ANCHOR_SELECTOR = (
    'a[href*="/groups/"][href*="/posts/"], '
    'a[href*="/groups/"][href*="/post/"], '
    'a[href*="/permalink/"], '
    'a[href*="multi_permalinks="], '
    'a[href*="story_fbid="], '
    'a[href*="set=gm."]'
)
FEED_ROOT_SELECTORS = (
    '[role="feed"]',
    'div[data-pagelet*="GroupsFeed"]',
    'div[data-pagelet*="FeedUnit"]',
    '[role="main"]',
)
POST_CONTAINER_CANDIDATE_SELECTORS = (
    POST_PERMALINK_ANCHOR_SELECTOR,
    '[role="feed"] [role="article"]',
    '[role="feed"] > div',
    'div[data-pagelet*="FeedUnit"]',
    'div[data-pagelet*="GroupsFeed"] [role="article"]',
    "[aria-posinset]",
)


def javascript_string_array(values: tuple[str, ...]) -> str:
    """把固定 selector tuple 輸出成安全的 JavaScript string array。"""

    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


FEED_ROOT_SELECTORS_SCRIPT = javascript_string_array(FEED_ROOT_SELECTORS)
POST_CONTAINER_CANDIDATE_SELECTORS_SCRIPT = javascript_string_array(
    POST_CONTAINER_CANDIDATE_SELECTORS
)


__all__ = [
    "FEED_ROOT_SELECTORS",
    "FEED_ROOT_SELECTORS_SCRIPT",
    "POST_CONTAINER_CANDIDATE_SELECTORS",
    "POST_CONTAINER_CANDIDATE_SELECTORS_SCRIPT",
    "POST_PERMALINK_ANCHOR_SELECTOR",
]
