"""Facebook sort control DOM fixture contract tests。"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

from facebook_monitor.facebook.sort_controls import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import FEED_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_controls import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import normalize_sort_adjust_result


GROUP_ID = "222518561920110"
POST_ID = "9999999999999999"


def test_feed_current_label_fixture_reads_group_sort_control() -> None:
    """posts current-label script 應從 group feed sort control 讀出目前排序。"""

    label = _evaluate_script(
        url=f"https://www.facebook.com/groups/{GROUP_ID}",
        html="""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main role="feed">
    <div role="button" aria-haspopup="menu">
      <h2>最相關</h2>
    </div>
  </main>
</body>
</html>
""",
        script=FEED_SORT_CURRENT_LABEL_SCRIPT,
    )

    assert label == "最相關"


def test_comment_current_label_fixture_ignores_option_description_text() -> None:
    """comments current-label script 不可把排序選項說明文字誤判成 current label。"""

    label = _evaluate_script(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{POST_ID}",
        html="""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="button" aria-haspopup="menu">
      <span dir="auto">最相關</span>
    </div>
    <div role="menu">
      <div role="menuitemradio">
        <span dir="auto">由新到舊</span>
        <span>最新的留言顯示在最上方</span>
      </div>
    </div>
  </main>
</body>
</html>
""",
        script=COMMENT_SORT_CURRENT_LABEL_SCRIPT,
    )

    assert label == "最相關"


def test_comment_current_label_fixture_ignores_button_option_description_before_control() -> None:
    """comments current-label script 不可先吃到含說明的 option-like button。"""

    label = _evaluate_script(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{POST_ID}",
        html="""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="button">
      <span dir="auto">由新到舊</span>
      <span>最新的留言顯示在最上方</span>
    </div>
    <div role="button" aria-haspopup="menu">
      <span dir="auto">最相關</span>
    </div>
  </main>
</body>
</html>
""",
        script=COMMENT_SORT_CURRENT_LABEL_SCRIPT,
    )

    assert label == "最相關"


def test_feed_sort_fallback_fixture_updates_to_preferred_label() -> None:
    """posts sort fallback 應能從本地 fixture 點選新貼文並確認 label。"""

    metadata = _evaluate_sort_metadata(
        url=f"https://www.facebook.com/groups/{GROUP_ID}",
        html="""
<!doctype html>
<html lang="zh-Hant">
<head>
  <style>
    [role="button"], [role="menu"], [role="menuitemradio"] {
      display: block;
      width: 220px;
      min-height: 32px;
    }
  </style>
</head>
<body>
  <main role="feed">
    <div id="feed-sort" role="button" aria-haspopup="menu">
      <h2>最相關</h2>
    </div>
    <div role="menu">
      <div role="menuitemradio" tabindex="0"
           onclick="document.querySelector('#feed-sort h2').textContent = '新貼文'">
        <span dir="auto">新貼文</span>
      </div>
    </div>
  </main>
</body>
</html>
""",
        script=FEED_SORT_ADJUST_SCRIPT,
        preferred_label=FEED_SORT_NEWEST_LABEL,
    )

    assert metadata["attempted"] is True
    assert metadata["changed"] is True
    assert metadata["before_label"] == "最相關"
    assert metadata["after_label"] == "新貼文"
    assert metadata["reason"] == "updated_to_preferred_sort"
    assert metadata["method"] == "js_fallback"
    assert metadata["target_kind"] == "posts"
    assert metadata["menu_opened"] is True
    assert metadata["preferred_option_count"] == 1
    assert metadata["clicked_option_text"] == "新貼文"


def test_feed_sort_fallback_fixture_reports_missing_preferred_option() -> None:
    """posts sort fallback 找不到 preferred option 時應保留候選文字。"""

    metadata = _evaluate_sort_metadata(
        url=f"https://www.facebook.com/groups/{GROUP_ID}",
        html="""
<!doctype html>
<html lang="zh-Hant">
<head>
  <style>
    [role="button"], [role="menu"], [role="menuitemradio"] {
      display: block;
      width: 220px;
      min-height: 32px;
    }
  </style>
</head>
<body>
  <main role="feed">
    <div role="button" aria-haspopup="menu"><h2>最相關</h2></div>
    <div role="menu">
      <div role="menuitemradio" tabindex="0"><span dir="auto">最新動態</span></div>
    </div>
  </main>
</body>
</html>
""",
        script=FEED_SORT_ADJUST_SCRIPT,
        preferred_label=FEED_SORT_NEWEST_LABEL,
    )

    assert metadata["attempted"] is True
    assert metadata["changed"] is False
    assert metadata["reason"] == "preferred_sort_option_not_found"
    assert metadata["failure_stage"] == "find_option"
    assert metadata["menu_opened"] is False
    assert metadata["preferred_option_count"] == 0
    assert "最新動態" in metadata["menu_candidate_texts"]


def test_comment_sort_fallback_fixture_accepts_description_text_option() -> None:
    """comments sort fallback 應接受含說明文字的由新到舊選項。"""

    metadata = _evaluate_sort_metadata(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{POST_ID}",
        html="""
<!doctype html>
<html lang="zh-Hant">
<head>
  <style>
    [role="button"], [role="menu"], [role="menuitemradio"] {
      display: block;
      width: 260px;
      min-height: 32px;
    }
  </style>
</head>
<body>
  <main>
    <div id="comment-sort" role="button" aria-haspopup="menu">
      <span dir="auto">最相關</span>
    </div>
    <div role="menu">
      <div role="menuitemradio" tabindex="0"
           onclick="document.querySelector('#comment-sort span').textContent = '由新到舊'">
        <span dir="auto">由新到舊</span>
        <span>最新的留言顯示在最上方</span>
      </div>
    </div>
  </main>
</body>
</html>
""",
        script=COMMENT_SORT_ADJUST_SCRIPT,
        preferred_label=COMMENT_SORT_NEWEST_LABEL,
    )

    assert metadata["attempted"] is True
    assert metadata["changed"] is True
    assert metadata["before_label"] == "最相關"
    assert metadata["after_label"] == "由新到舊"
    assert metadata["reason"] == "updated_to_preferred_sort"
    assert metadata["method"] == "js_fallback"
    assert metadata["target_kind"] == "comments"
    assert metadata["menu_opened"] is True
    assert metadata["clicked_option_text"] == "由新到舊"


def _evaluate_sort_metadata(
    *,
    url: str,
    html: str,
    script: str,
    preferred_label: str,
) -> dict[str, Any]:
    """用 Chromium fixture 執行 sort script 並回傳 normalization metadata。"""

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, timeout=10_000)
        except PlaywrightError as exc:
            pytest.skip(f"chromium browser is not installed: {exc}")
        try:
            page = browser.new_page()
            _fulfill_fixture_route(page, url=url, html=html)
            page.goto(url)
            payload = page.evaluate(script, preferred_label)
        finally:
            browser.close()
    if not isinstance(payload, dict):
        raise AssertionError("sort script did not return an object payload")
    return normalize_sort_adjust_result(payload, preferred_label=preferred_label).to_metadata()


def _evaluate_script(
    *,
    url: str,
    html: str,
    script: str,
) -> object:
    """用 Chromium fixture 執行任意 sort DOM script。"""

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, timeout=10_000)
        except PlaywrightError as exc:
            pytest.skip(f"chromium browser is not installed: {exc}")
        try:
            page = browser.new_page()
            _fulfill_fixture_route(page, url=url, html=html)
            page.goto(url)
            return page.evaluate(script)
        finally:
            browser.close()


def _fulfill_fixture_route(page: Page, *, url: str, html: str) -> None:
    """用 fixture HTML 回應 Facebook URL，讓 sort script 可讀正確 location。"""

    page.route(
        f"{url.rstrip('/')}/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html,
        ),
    )
    page.route(
        url,
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html,
        ),
    )
