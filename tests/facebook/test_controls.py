"""Facebook control helper tests。"""

from __future__ import annotations

import json
from pathlib import Path

from facebook_monitor.facebook.scroll_controls import SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_controls import COMMENT_SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_controls import BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT
from facebook_monitor.facebook.scroll_controls import RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT
from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_mutations import COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_LABELS
from facebook_monitor.facebook.sort_controls import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import ensure_preferred_comment_sort
from facebook_monitor.facebook.sort_controls import normalize_sort_adjust_result


class FakeCommentSortFallbackPage:
    """模擬 Playwright native click 成功切換留言排序。"""

    def __init__(self) -> None:
        self.current_label = "最相關"
        self.role_clicks: list[tuple[str, str]] = []
        self.text_clicks: list[str] = []

    def evaluate(self, script: str, _preferred_label: str | None = None) -> object:
        if script == COMMENT_SORT_CURRENT_LABEL_SCRIPT:
            return self.current_label
        return {
            "attempted": True,
            "changed": False,
            "preferredLabel": "由新到舊",
            "beforeLabel": "最相關",
            "afterLabel": "最相關",
            "reason": "preferred_sort_option_not_found",
            "mutationSuppressionMs": 3200,
            "mutationSuppressionReason": "auto_adjust_sort",
            "menuCandidateTexts": ["最相關"],
        }

    def get_by_role(self, role: str, *, name: str) -> "FakeCommentSortLocator":
        self.role_clicks.append((role, name))
        return FakeCommentSortLocator(self, "control")

    def get_by_text(self, text: str, *, exact: bool) -> "FakeCommentSortLocator":
        assert not exact
        self.text_clicks.append(text)
        return FakeCommentSortLocator(self, "option")

    def wait_for_timeout(self, _ms: int) -> None:
        return None


class FakeCommentSortLocator:
    """提供 Playwright locator first.click 的最小替身。"""

    def __init__(self, page: FakeCommentSortFallbackPage, kind: str) -> None:
        self._page = page
        self._kind = kind

    @property
    def first(self) -> "FakeCommentSortLocator":
        return self

    def click(self, *, timeout: int) -> None:
        assert timeout == 3000
        if self._kind == "option":
            self._page.current_label = "由新到舊"


def test_feed_sort_labels_match_userscript_final_labels() -> None:
    """feed sort 常數需對齊 userscript 成熟版 label。"""

    assert FEED_SORT_NEWEST_LABEL == "新貼文"
    assert FEED_SORT_LABELS == ("新貼文", "最相關", "最新動態")
    assert "熱門貼文" not in FEED_SORT_ADJUST_SCRIPT
    assert "最近動態" not in FEED_SORT_ADJUST_SCRIPT
    assert "最相關" in FEED_SORT_ADJUST_SCRIPT
    assert "最新動態" in FEED_SORT_ADJUST_SCRIPT


def test_sort_script_uses_exact_feed_option_chain() -> None:
    """排序選項判斷需保留 JS 的 scan target 與 known label chain。"""

    assert "isSortMenuOptionForLabel" in FEED_SORT_ADJUST_SCRIPT
    assert "optionLabels.includes(text)" in FEED_SORT_ADJUST_SCRIPT
    assert "const getCurrentScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "getPreferredSortLabelForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "getCurrentSortControlForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "findPreferredSortMenuOptionForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "unsupported_scan_target" in FEED_SORT_ADJUST_SCRIPT
    assert 'suppressMutationsForMs(3200, "auto_adjust_sort")' in FEED_SORT_ADJUST_SCRIPT
    assert "getSelectorElementsByOrder" in FEED_SORT_ADJUST_SCRIPT


def test_comment_sort_labels_and_script_match_userscript_semantics() -> None:
    """comments sort 必須使用 JS 成熟版由新到舊與說明文字判斷鏈。"""

    assert COMMENT_SORT_NEWEST_LABEL == "由新到舊"
    assert COMMENT_SORT_LABELS == ("由新到舊", "最相關", "所有留言")
    assert "isLikelyCommentSortOptionText" in COMMENT_SORT_ADJUST_SCRIPT
    assert "getCurrentCommentSortControl" in COMMENT_SORT_ADJUST_SCRIPT
    assert "findCommentSortMenuOption" in COMMENT_SORT_ADJUST_SCRIPT
    assert "最新的留言顯示在最上方" in COMMENT_SORT_ADJUST_SCRIPT
    assert 'suppressMutationsForMs(3200, "auto_adjust_sort")' in COMMENT_SORT_ADJUST_SCRIPT


def test_comment_sort_script_waits_and_keeps_failure_candidates() -> None:
    """comments sort 失敗時需留下選單候選文字，並避免固定短等待。"""

    assert "waitForPreferredSortOptionForScanTarget" in COMMENT_SORT_ADJUST_SCRIPT
    assert "timeoutMs = 1800" in COMMENT_SORT_ADJUST_SCRIPT
    assert "intervalMs = 120" in COMMENT_SORT_ADJUST_SCRIPT
    assert "await sleep(360)" not in COMMENT_SORT_ADJUST_SCRIPT
    assert "collectVisibleSortCandidateTexts" in COMMENT_SORT_ADJUST_SCRIPT
    assert "menuCandidateTexts: collectVisibleSortCandidateTexts()" in COMMENT_SORT_ADJUST_SCRIPT
    assert 'role === "menuitem"' in COMMENT_SORT_ADJUST_SCRIPT
    assert 'role === "option"' in COMMENT_SORT_ADJUST_SCRIPT
    assert "if (isMenuLike) return true;" in COMMENT_SORT_ADJUST_SCRIPT


def test_comment_sort_uses_native_click_before_js_fallback() -> None:
    """comments path 優先用 Playwright trusted click，避免 JS click 打不開 menu。"""

    page = FakeCommentSortFallbackPage()

    result = ensure_preferred_comment_sort(page, enabled=True)

    assert page.role_clicks == [("button", "最相關")]
    assert page.text_clicks == ["由新到舊"]
    assert result.changed
    assert result.before_label == "最相關"
    assert result.after_label == "由新到舊"
    assert result.reason == "updated_to_preferred_sort"


def test_sort_adjust_result_preserves_menu_candidate_texts() -> None:
    """sort diagnostics 需保留 comments menu candidate texts 供下輪判讀。"""

    result = normalize_sort_adjust_result(
        {
            "attempted": True,
            "changed": False,
            "preferredLabel": "由新到舊",
            "beforeLabel": "最相關",
            "afterLabel": "最相關",
            "reason": "preferred_sort_option_not_found",
            "mutationSuppressionMs": 3200,
            "mutationSuppressionReason": "auto_adjust_sort",
            "menuCandidateTexts": ["最相關", "由新到舊 最新留言會顯示在最上方"],
        },
        preferred_label="由新到舊",
    )

    assert result.menu_candidate_texts == ("最相關", "由新到舊 最新留言會顯示在最上方")
    assert result.to_metadata()["menu_candidate_texts"] == [
        "最相關",
        "由新到舊 最新留言會顯示在最上方",
    ]


def test_sort_adjust_result_matches_golden_fixture() -> None:
    """sort result normalization golden fixture 鎖住 diagnostics shape。"""

    fixture_path = Path("tests/fixtures/facebook/sort_adjust_golden.json")
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        result = normalize_sort_adjust_result(
            case["payload"],
            preferred_label=case["preferred_label"],
        )
        metadata = result.to_metadata()
        expected = case["expected"]
        assert result.attempted is expected["attempted"], case["name"]
        assert result.changed is expected["changed"], case["name"]
        assert result.preferred_label == expected["preferred_label"], case["name"]
        assert result.before_label == expected["before_label"], case["name"]
        assert result.after_label == expected["after_label"], case["name"]
        assert result.reason == expected["reason"], case["name"]
        assert metadata["menu_candidate_texts"] == expected["menu_candidate_texts"], case["name"]


def test_scroll_script_uses_target_by_and_restore_snapshot() -> None:
    """auto_load_more 不應退回單純 window.scrollBy，需有 target 與 restore 語義。"""

    assert "getLoadMoreScrollTarget" in SCROLL_LOAD_MORE_SCRIPT
    assert "isScrollableElement" in SCROLL_LOAD_MORE_SCRIPT
    assert "findScrollableAncestor" in SCROLL_LOAD_MORE_SCRIPT
    assert "scrollTargetBy" in SCROLL_LOAD_MORE_SCRIPT
    assert "performConfiguredLoadMore" in SCROLL_LOAD_MORE_SCRIPT
    assert "getLoadMoreMode" in SCROLL_LOAD_MORE_SCRIPT
    assert "movedDistance" in SCROLL_LOAD_MORE_SCRIPT
    assert "window.__facebookMonitorLoadMoreSnapshot" in RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT


def test_comment_scroll_script_uses_nested_targets_and_guard() -> None:
    """comments load-more 必須保留 nested scroll target、scoring 與 guard 語義。"""

    assert "collectCommentScrollTargets" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "scoreCommentScrollElement" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "scrollFirstMovable" not in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "comment_nested_scroll" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "movedDistance" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "window.__facebookMonitorScanRuntime" in BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT
    assert "isLoadingMoreComments" in BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT


def test_comment_mutation_relevance_helpers_match_userscript_chain() -> None:
    """comments mutation relevance 需保留 permalink/text/suppression 判斷鏈。"""

    assert "elementHasCommentMutationSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "elementHasCommentTextMutationSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "mutationTargetHasDirectCommentSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "mutationsHaveRelevantCommentNodes" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "shouldRescanForCommentMutation" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "__facebookMonitorMutationSuppression" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT


def test_feed_dom_returns_collected_meta_shape() -> None:
    """DOM extractor 需回傳候選與過濾統計，供 collected_meta 彙整。"""

    assert "return { items, meta };" in POST_LIKE_ITEMS_SCRIPT
    assert "candidateCount: nodes.length" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredEmptyTextCount" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredNonPostCount" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredFeedSortControlCount" in POST_LIKE_ITEMS_SCRIPT
    assert "postsWithPostIdCount" in POST_LIKE_ITEMS_SCRIPT
