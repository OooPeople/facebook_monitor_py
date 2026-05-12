"""Facebook feed DOM extraction public entrypoint.

職責：保留既有 import path；大型 page.evaluate script 放在
`feed_dom_scripts.py`，後續可再按 selectors / permalink / text helper 拆分。
"""

from __future__ import annotations

from facebook_monitor.facebook.feed_dom_scripts import (
    POST_LIKE_ITEMS_SCRIPT as POST_LIKE_ITEMS_SCRIPT,
)

__all__ = ["POST_LIKE_ITEMS_SCRIPT"]
