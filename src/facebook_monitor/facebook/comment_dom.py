"""Facebook comments DOM extraction public entrypoint.

職責：保留既有 import path；大型 page.evaluate script 放在
`comment_dom_scripts.py`，避免 extractor 與 DOM script payload 繼續混在同層入口。
"""

from facebook_monitor.facebook.comment_dom_scripts import (
    COMMENTS_LIKE_ITEMS_SCRIPT as COMMENTS_LIKE_ITEMS_SCRIPT,
)

__all__ = ["COMMENTS_LIKE_ITEMS_SCRIPT"]
