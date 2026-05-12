"""Facebook feed DOM extraction script payloads.

職責：組裝短生命週期 page.evaluate 使用的 posts DOM 抽取腳本；
各段 JS 片段依責任拆到 `feed_dom_*_script.py`。
"""

from __future__ import annotations

from facebook_monitor.facebook.feed_dom_bootstrap_script import FEED_DOM_BOOTSTRAP_SCRIPT
from facebook_monitor.facebook.feed_dom_collector_script import FEED_DOM_COLLECTOR_SCRIPT
from facebook_monitor.facebook.feed_dom_diagnostics_script import FEED_DOM_DIAGNOSTICS_SCRIPT
from facebook_monitor.facebook.feed_dom_permalink_script import FEED_DOM_PERMALINK_SCRIPT
from facebook_monitor.facebook.feed_dom_scope_script import FEED_DOM_SCOPE_SCRIPT
from facebook_monitor.facebook.feed_dom_text_script import FEED_DOM_TEXT_SCRIPT
from facebook_monitor.facebook.feed_dom_warmup_script import FEED_DOM_WARMUP_SCRIPT
from facebook_monitor.facebook.text_snippet_dom import TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT

POST_LIKE_ITEMS_SCRIPT = (
    FEED_DOM_BOOTSTRAP_SCRIPT
    + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT
    + FEED_DOM_TEXT_SCRIPT
    + FEED_DOM_PERMALINK_SCRIPT
    + FEED_DOM_DIAGNOSTICS_SCRIPT
    + FEED_DOM_WARMUP_SCRIPT
    + FEED_DOM_SCOPE_SCRIPT
    + FEED_DOM_COLLECTOR_SCRIPT
)

__all__ = ["POST_LIKE_ITEMS_SCRIPT"]
