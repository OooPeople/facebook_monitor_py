"""DOM script fragment assembly tests。"""

from __future__ import annotations

from facebook_monitor.facebook.comment_dom import COMMENTS_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_dom_author_script import COMMENT_DOM_AUTHOR_SCRIPT
from facebook_monitor.facebook.comment_dom_bootstrap_script import COMMENT_DOM_BOOTSTRAP_SCRIPT
from facebook_monitor.facebook.comment_dom_collector_script import COMMENT_DOM_COLLECTOR_SCRIPT
from facebook_monitor.facebook.comment_dom_permalink_script import COMMENT_DOM_PERMALINK_SCRIPT
from facebook_monitor.facebook.comment_dom_scope_script import COMMENT_DOM_SCOPE_SCRIPT
from facebook_monitor.facebook.comment_dom_text_extraction_script import (
    COMMENT_DOM_TEXT_EXTRACTION_SCRIPT,
)
from facebook_monitor.facebook.comment_dom_text_script import COMMENT_DOM_TEXT_CLEANUP_SCRIPT
from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.feed_dom_bootstrap_script import FEED_DOM_BOOTSTRAP_SCRIPT
from facebook_monitor.facebook.feed_dom_collector_script import FEED_DOM_COLLECTOR_SCRIPT
from facebook_monitor.facebook.feed_dom_diagnostics_script import FEED_DOM_DIAGNOSTICS_SCRIPT
from facebook_monitor.facebook.feed_dom_permalink_script import FEED_DOM_PERMALINK_SCRIPT
from facebook_monitor.facebook.feed_dom_scope_script import FEED_DOM_SCOPE_SCRIPT
from facebook_monitor.facebook.feed_dom_text_script import FEED_DOM_TEXT_SCRIPT
from facebook_monitor.facebook.feed_dom_warmup_script import FEED_DOM_WARMUP_SCRIPT
from facebook_monitor.facebook.scroll_comment_scripts import (
    COMMENT_SCROLL_HELPERS_SCRIPT as COMMENT_SCROLL_HELPERS_FRAGMENT,
)
from facebook_monitor.facebook.scroll_comment_scripts import (
    COMMENT_SCROLL_LOAD_MORE_SCRIPT as COMMENT_SCROLL_LOAD_MORE_FRAGMENT,
)
from facebook_monitor.facebook.scroll_control_scripts import COMMENT_SCROLL_HELPERS_SCRIPT
from facebook_monitor.facebook.scroll_control_scripts import COMMENT_SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_control_scripts import SCROLL_HELPERS_SCRIPT
from facebook_monitor.facebook.scroll_control_scripts import SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_guard_scripts import (
    BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT as BEGIN_COMMENT_LOAD_MORE_GUARD_FRAGMENT,
)
from facebook_monitor.facebook.scroll_post_scripts import (
    SCROLL_HELPERS_SCRIPT as SCROLL_HELPERS_FRAGMENT,
)
from facebook_monitor.facebook.scroll_post_scripts import (
    SCROLL_LOAD_MORE_SCRIPT as SCROLL_LOAD_MORE_FRAGMENT,
)
from facebook_monitor.facebook.text_snippet_dom import TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT


def test_comment_dom_script_is_assembled_from_ordered_fragments() -> None:
    """comments DOM payload 必須按固定片段順序組裝，避免漏掉 target scope。"""

    expected = (
        COMMENT_DOM_BOOTSTRAP_SCRIPT
        + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT
        + COMMENT_DOM_TEXT_CLEANUP_SCRIPT
        + COMMENT_DOM_PERMALINK_SCRIPT
        + COMMENT_DOM_SCOPE_SCRIPT
        + COMMENT_DOM_TEXT_EXTRACTION_SCRIPT
        + COMMENT_DOM_AUTHOR_SCRIPT
        + COMMENT_DOM_COLLECTOR_SCRIPT
    )

    assert COMMENTS_LIKE_ITEMS_SCRIPT == expected
    assert COMMENTS_LIKE_ITEMS_SCRIPT.lstrip().startswith("(payload) => {")
    assert COMMENTS_LIKE_ITEMS_SCRIPT.rstrip().endswith("}")


def test_comment_dom_fragments_keep_responsibility_markers() -> None:
    """comments fragment 名稱與內容需維持可辨識責任。"""

    assert "const scanTarget" in COMMENT_DOM_BOOTSTRAP_SCRIPT
    assert "cleanCommentExtractedText" in COMMENT_DOM_TEXT_CLEANUP_SCRIPT
    assert "buildCanonicalGroupCommentUrl" in COMMENT_DOM_PERMALINK_SCRIPT
    assert "collectCommentSearchRoots" in COMMENT_DOM_SCOPE_SCRIPT
    assert "evaluateCommentTargetScope" in COMMENT_DOM_SCOPE_SCRIPT
    assert "extractCommentTextDetails" in COMMENT_DOM_TEXT_EXTRACTION_SCRIPT
    assert "extractCommentAuthor" in COMMENT_DOM_AUTHOR_SCRIPT
    assert "comments_visible_window" in COMMENT_DOM_COLLECTOR_SCRIPT
    assert "filteredOutOfScopeCount" in COMMENT_DOM_COLLECTOR_SCRIPT


def test_feed_dom_script_is_assembled_from_ordered_fragments() -> None:
    """posts DOM payload 必須按固定片段順序組裝，避免 warmup / diagnostics 掉失。"""

    expected = (
        FEED_DOM_BOOTSTRAP_SCRIPT
        + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT
        + FEED_DOM_TEXT_SCRIPT
        + FEED_DOM_PERMALINK_SCRIPT
        + FEED_DOM_DIAGNOSTICS_SCRIPT
        + FEED_DOM_WARMUP_SCRIPT
        + FEED_DOM_SCOPE_SCRIPT
        + FEED_DOM_COLLECTOR_SCRIPT
    )

    assert POST_LIKE_ITEMS_SCRIPT == expected
    assert POST_LIKE_ITEMS_SCRIPT.lstrip().startswith("async (maxItems) => {")
    assert POST_LIKE_ITEMS_SCRIPT.rstrip().endswith("}")
    assert r"/\\/+$" not in POST_LIKE_ITEMS_SCRIPT
    assert r"^\\/groups\\/" not in POST_LIKE_ITEMS_SCRIPT
    assert r"/\/+$" in POST_LIKE_ITEMS_SCRIPT
    assert r"^\/groups\/" in POST_LIKE_ITEMS_SCRIPT


def test_feed_dom_fragments_keep_responsibility_markers() -> None:
    """posts fragment 名稱與內容需維持可辨識責任。"""

    assert "const feedRoots" in FEED_DOM_BOOTSTRAP_SCRIPT
    assert "extractPostTextDetails" in FEED_DOM_TEXT_SCRIPT
    assert "expandCollapsedPostText" in FEED_DOM_TEXT_SCRIPT
    assert "extractCanonicalPermalinkFromHref" in FEED_DOM_PERMALINK_SCRIPT
    assert "collectLinkDiagnostics" in FEED_DOM_DIAGNOSTICS_SCRIPT
    assert "collectPermalinkWarmupDiagnostics" in FEED_DOM_DIAGNOSTICS_SCRIPT
    assert "warmPermalinkAnchors" in FEED_DOM_WARMUP_SCRIPT
    assert "collectPermalinkSearchScopes" in FEED_DOM_SCOPE_SCRIPT
    assert "const candidateNodes" in FEED_DOM_COLLECTOR_SCRIPT
    assert "return { items, meta };" in FEED_DOM_COLLECTOR_SCRIPT


def test_scroll_control_scripts_reexport_split_payloads() -> None:
    """scroll control facade 必須 re-export posts / comments payload。"""

    assert SCROLL_HELPERS_SCRIPT == SCROLL_HELPERS_FRAGMENT
    assert SCROLL_LOAD_MORE_SCRIPT == SCROLL_LOAD_MORE_FRAGMENT
    assert COMMENT_SCROLL_HELPERS_SCRIPT == COMMENT_SCROLL_HELPERS_FRAGMENT
    assert COMMENT_SCROLL_LOAD_MORE_SCRIPT == COMMENT_SCROLL_LOAD_MORE_FRAGMENT


def test_scroll_fragments_keep_responsibility_markers() -> None:
    """scroll fragment 需保留 posts、comments 與 guard 的核心語義。"""

    assert "getLoadMoreScrollTarget" in SCROLL_HELPERS_FRAGMENT
    assert "performConfiguredLoadMore" in SCROLL_LOAD_MORE_FRAGMENT
    assert "collectCommentScrollTargets" in COMMENT_SCROLL_HELPERS_FRAGMENT
    assert "comment_nested_scroll" in COMMENT_SCROLL_LOAD_MORE_FRAGMENT
    assert "window.__facebookMonitorScanRuntime" in BEGIN_COMMENT_LOAD_MORE_GUARD_FRAGMENT
