"""Facebook comments DOM extractor script payload。

職責：組裝 comments 可見留言抽取、comment canonical URL 與文字清理語義的
page.evaluate payload；各段 JS 片段依責任拆到 `comment_dom_*_script.py`。
不包含留言排序、滾動載入更多或 mutation relevance。
"""

from facebook_monitor.facebook.comment_dom_author_script import COMMENT_DOM_AUTHOR_SCRIPT
from facebook_monitor.facebook.comment_dom_bootstrap_script import COMMENT_DOM_BOOTSTRAP_SCRIPT
from facebook_monitor.facebook.comment_dom_collector_script import COMMENT_DOM_COLLECTOR_SCRIPT
from facebook_monitor.facebook.comment_dom_permalink_script import COMMENT_DOM_PERMALINK_SCRIPT
from facebook_monitor.facebook.comment_dom_scope_script import COMMENT_DOM_SCOPE_SCRIPT
from facebook_monitor.facebook.comment_dom_text_extraction_script import (
    COMMENT_DOM_TEXT_EXTRACTION_SCRIPT,
)
from facebook_monitor.facebook.comment_dom_text_script import COMMENT_DOM_TEXT_CLEANUP_SCRIPT
from facebook_monitor.facebook.text_snippet_dom import TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT

COMMENTS_LIKE_ITEMS_SCRIPT = (
    COMMENT_DOM_BOOTSTRAP_SCRIPT
    + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT
    + COMMENT_DOM_TEXT_CLEANUP_SCRIPT
    + COMMENT_DOM_PERMALINK_SCRIPT
    + COMMENT_DOM_SCOPE_SCRIPT
    + COMMENT_DOM_TEXT_EXTRACTION_SCRIPT
    + COMMENT_DOM_AUTHOR_SCRIPT
    + COMMENT_DOM_COLLECTOR_SCRIPT
)

__all__ = ["COMMENTS_LIKE_ITEMS_SCRIPT"]
