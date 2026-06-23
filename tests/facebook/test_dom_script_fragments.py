"""DOM script fragment assembly tests。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from facebook_monitor.facebook.comment_dom_scripts import COMMENTS_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_dom_author_script import COMMENT_DOM_AUTHOR_SCRIPT
from facebook_monitor.facebook.comment_dom_bootstrap_script import COMMENT_DOM_BOOTSTRAP_SCRIPT
from facebook_monitor.facebook.comment_dom_collector_script import COMMENT_DOM_COLLECTOR_SCRIPT
from facebook_monitor.facebook.comment_dom_permalink_script import COMMENT_DOM_PERMALINK_SCRIPT
from facebook_monitor.facebook.comment_dom_scope_script import COMMENT_DOM_SCOPE_SCRIPT
from facebook_monitor.facebook.comment_dom_text_extraction_script import (
    COMMENT_DOM_TEXT_EXTRACTION_SCRIPT,
)
from facebook_monitor.facebook.comment_dom_text_script import COMMENT_DOM_TEXT_CLEANUP_SCRIPT
from facebook_monitor.facebook.feed_dom_scripts import POST_LIKE_ITEMS_SCRIPT
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
from facebook_monitor.facebook.scroll_guard_scripts import (
    BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT as BEGIN_COMMENT_LOAD_MORE_GUARD_FRAGMENT,
)
from facebook_monitor.facebook.scroll_post_scripts import (
    SCROLL_HELPERS_SCRIPT as SCROLL_HELPERS_FRAGMENT,
)
from facebook_monitor.facebook.scroll_post_scripts import (
    SCROLL_LOAD_MORE_SCRIPT as SCROLL_LOAD_MORE_FRAGMENT,
)
from facebook_monitor.facebook.permalink_dom import PERMALINK_DOM_HELPERS_SCRIPT
from facebook_monitor.facebook.text_cleanup_dom import TEXT_CLEANUP_HELPERS_SCRIPT
from facebook_monitor.facebook.text_snippet_dom import TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT


def _run_permalink_helper_cases(cases: list[dict[str, str]]) -> list[dict[str, str]]:
    """用 Node 執行 permalink helper，鎖住共用 JS 的實際輸出。"""

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is required for permalink DOM behavior tests")
    script = f"""
globalThis.location = new URL("https://www.facebook.com/groups/222518561920110");
const helpers = (() => {{
{PERMALINK_DOM_HELPERS_SCRIPT}
    return {{
        extractCanonicalPermalinkFromHref,
        extractGroupPostRouteIdFromUrl,
        normalizeFacebookUrl,
    }};
}})();
const cases = {json.dumps(cases, ensure_ascii=False)};
const results = cases.map((item) => {{
    if (item.kind === "routePostId") {{
        const url = helpers.normalizeFacebookUrl(item.href);
        return {{ value: helpers.extractGroupPostRouteIdFromUrl(url, item.expectedGroupId || "") }};
    }}
    return helpers.extractCanonicalPermalinkFromHref(item.href, item.expectedGroupId || "");
}});
console.log(JSON.stringify(results));
"""
    result = subprocess.run(
        [node_bin, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    return json.loads(result.stdout)


def _run_comment_text_cleanup_cases(values: list[str]) -> list[str]:
    """用 Node 執行 comments 文字清理片段，確認清理順序符合 extractor 語義。"""

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is required for comment text cleanup behavior tests")
    script = f"""
const helpers = (() => {{
    const commentActionTrail = [];
    const nonBodyLabels = [];
{TEXT_CLEANUP_HELPERS_SCRIPT}
{COMMENT_DOM_TEXT_CLEANUP_SCRIPT}
    return {{ cleanCommentExtractedText }};
}})();
const values = {json.dumps(values, ensure_ascii=False)};
console.log(JSON.stringify(values.map((value) => helpers.cleanCommentExtractedText(value))));
"""
    result = subprocess.run(
        [node_bin, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    return json.loads(result.stdout)


def test_comment_dom_script_is_assembled_from_ordered_fragments() -> None:
    """comments DOM payload 必須按固定片段順序組裝，避免漏掉 target scope。"""

    expected = (
        COMMENT_DOM_BOOTSTRAP_SCRIPT
        + TEXT_CLEANUP_HELPERS_SCRIPT
        + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT
        + PERMALINK_DOM_HELPERS_SCRIPT
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
    assert "stripFacebookExpandCollapseLabels" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "cleanSharedFacebookMultilineText" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "顯示較少" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "顯示更少" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "See less" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "cleanCommentExtractedText" in COMMENT_DOM_TEXT_CLEANUP_SCRIPT
    assert "cleanCommentExtractedDisplayText" in COMMENT_DOM_TEXT_CLEANUP_SCRIPT
    assert "collapseRepeatedAdjacentText" not in COMMENT_DOM_TEXT_CLEANUP_SCRIPT
    assert "buildCanonicalGroupCommentUrl" in PERMALINK_DOM_HELPERS_SCRIPT
    assert "extractCommentIdFromValue" in PERMALINK_DOM_HELPERS_SCRIPT
    assert "function normalizeFacebookUrl" not in COMMENT_DOM_PERMALINK_SCRIPT
    assert "collectCommentSearchRoots" in COMMENT_DOM_SCOPE_SCRIPT
    assert "evaluateCommentTargetScope" in COMMENT_DOM_SCOPE_SCRIPT
    assert "extractCommentTextDetails" in COMMENT_DOM_TEXT_EXTRACTION_SCRIPT
    assert "extractCommentAuthor" in COMMENT_DOM_AUTHOR_SCRIPT
    assert "comments_visible_window" in COMMENT_DOM_COLLECTOR_SCRIPT
    assert "filteredOutOfScopeCount" in COMMENT_DOM_COLLECTOR_SCRIPT


def test_comment_dom_text_cleanup_collapses_repeated_text_with_ui_labels() -> None:
    """comments DOM 清理需先保留重複折疊機會，再移除尾端 UI label。"""

    repeated = (
        "這是一則有票券關鍵字的留言 顯示較少 "
        "這是一則有票券關鍵字的留言 顯示較少"
    )

    assert _run_comment_text_cleanup_cases(
        [
            repeated,
            "這是一則有票券關鍵字的留言 顯示較少",
            "顯示更多資訊請看留言",
        ]
    ) == [
        "這是一則有票券關鍵字的留言",
        "這是一則有票券關鍵字的留言",
        "顯示更多資訊請看留言",
    ]


def test_feed_dom_script_is_assembled_from_ordered_fragments() -> None:
    """posts DOM payload 必須按固定片段順序組裝，避免 warmup / diagnostics 掉失。"""

    expected = (
        FEED_DOM_BOOTSTRAP_SCRIPT
        + TEXT_CLEANUP_HELPERS_SCRIPT
        + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT
        + PERMALINK_DOM_HELPERS_SCRIPT
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
    assert "cleanSharedFacebookText" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "cleanSharedFacebookMultilineText" in TEXT_CLEANUP_HELPERS_SCRIPT
    assert "extractPostTextDetails" in FEED_DOM_TEXT_SCRIPT
    assert "expandCollapsedPostText" in FEED_DOM_TEXT_SCRIPT
    assert "collapseRepeatedAdjacentText" not in FEED_DOM_TEXT_SCRIPT
    assert "extractCanonicalPermalinkFromHref" in PERMALINK_DOM_HELPERS_SCRIPT
    assert "buildCanonicalGroupPostUrl" in PERMALINK_DOM_HELPERS_SCRIPT
    assert "extractCanonicalPermalinkFromHref" not in FEED_DOM_PERMALINK_SCRIPT
    assert "collectLinkDiagnostics" in FEED_DOM_DIAGNOSTICS_SCRIPT
    assert "collectPermalinkWarmupDiagnostics" in FEED_DOM_DIAGNOSTICS_SCRIPT
    assert "warmPermalinkAnchors" in FEED_DOM_WARMUP_SCRIPT
    assert "collectPermalinkSearchScopes" in FEED_DOM_SCOPE_SCRIPT
    assert "const candidateNodes" in FEED_DOM_COLLECTOR_SCRIPT
    assert "return { items, meta };" in FEED_DOM_COLLECTOR_SCRIPT


def test_shared_permalink_helper_preserves_comment_gm_priority() -> None:
    """comments route post id 需保留 gm.<id> 優先，避免共用化吃掉差異。"""

    assert "function extractGroupRouteQueryPostId(url, preferGmPostId = false)" in (
        PERMALINK_DOM_HELPERS_SCRIPT
    )
    assert "return extractGroupRouteQueryPostId(url, true);" in PERMALINK_DOM_HELPERS_SCRIPT
    prefer_gm_block = PERMALINK_DOM_HELPERS_SCRIPT.split("const patterns = preferGmPostId", 1)[
        1
    ].split(": [", 1)[0]
    assert prefer_gm_block.index(r"/\bgm\.(\d+)/i") < prefer_gm_block.index(
        r"/\b(\d{8,})\b/"
    )


def test_shared_permalink_helper_behavior_preserves_post_and_comment_routes() -> None:
    """共用 permalink helper 需保留 posts/comments 既有 URL 解析差異。"""

    cases = [
        {
            "href": "https://www.facebook.com/groups/222518561920110/posts/1111111111111111/",
            "expectedGroupId": "222518561920110",
        },
        {
            "href": "https://www.facebook.com/groups/222518561920110/permalink/2222222222222222",
            "expectedGroupId": "222518561920110",
        },
        {
            "href": "https://www.facebook.com/groups/222518561920110/posts/pcb.3333333333333333",
            "expectedGroupId": "222518561920110",
        },
        {
            "href": "https://www.facebook.com/photo.php?idorvanity=222518561920110&set=gm.4444444444444444",
            "expectedGroupId": "222518561920110",
        },
        {
            "href": "https://www.facebook.com/permalink.php?story_fbid=5555555555555555&id=222518561920110",
            "expectedGroupId": "222518561920110",
        },
        {
            "kind": "routePostId",
            "href": "https://www.facebook.com/groups/222518561920110?set=a.1111111111111111.gm.2187454285426518",
            "expectedGroupId": "222518561920110",
        },
    ]

    assert _run_permalink_helper_cases(cases) == [
        {
            "permalink": (
                "https://www.facebook.com/groups/222518561920110/posts/1111111111111111"
            ),
            "source": "groups_post_anchor",
        },
        {
            "permalink": (
                "https://www.facebook.com/groups/222518561920110/posts/2222222222222222"
            ),
            "source": "group_permalink_anchor",
        },
        {
            "permalink": (
                "https://www.facebook.com/groups/222518561920110/posts/3333333333333333"
            ),
            "source": "pcb_anchor",
        },
        {
            "permalink": (
                "https://www.facebook.com/groups/222518561920110/posts/4444444444444444"
            ),
            "source": "photo_gm_anchor",
        },
        {
            "permalink": (
                "https://www.facebook.com/groups/222518561920110/posts/5555555555555555"
            ),
            "source": "permalink_php_anchor",
        },
        {"value": "2187454285426518"},
    ]


def test_shared_permalink_helper_rejects_expected_group_mismatch() -> None:
    """JS permalink helper 不可在 expected group mismatch 時產生 permalink。"""

    cases = [
        {
            "href": "https://www.facebook.com/groups/222518561920110/posts/1111111111111111/",
            "expectedGroupId": "999999999999999",
        },
        {
            "href": "https://www.facebook.com/photo.php?idorvanity=222518561920110&set=gm.4444444444444444",
            "expectedGroupId": "999999999999999",
        },
        {
            "href": "https://www.facebook.com/permalink.php?story_fbid=5555555555555555&id=222518561920110",
            "expectedGroupId": "999999999999999",
        },
    ]

    assert _run_permalink_helper_cases(cases) == [
        {"permalink": "", "source": "unavailable"},
        {"permalink": "", "source": "unavailable"},
        {"permalink": "", "source": "unavailable"},
    ]


def test_scroll_fragments_keep_responsibility_markers() -> None:
    """scroll fragment 需保留 posts、comments 與 guard 的核心語義。"""

    assert "getLoadMoreScrollTarget" in SCROLL_HELPERS_FRAGMENT
    assert "performConfiguredLoadMore" in SCROLL_LOAD_MORE_FRAGMENT
    assert "getCommentScrollElement" not in SCROLL_HELPERS_FRAGMENT
    assert "comment_id=" not in SCROLL_HELPERS_FRAGMENT
    assert "collectCommentScrollTargets" in COMMENT_SCROLL_HELPERS_FRAGMENT
    assert "comment_nested_scroll" in COMMENT_SCROLL_LOAD_MORE_FRAGMENT
    assert "window.__facebookMonitorScanRuntime" in BEGIN_COMMENT_LOAD_MORE_GUARD_FRAGMENT
