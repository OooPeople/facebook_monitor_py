"""Facebook page guard 的真實 browser-side DOM contract tests。"""

from __future__ import annotations

from collections.abc import Iterator
import os
from typing import Any
from typing import NoReturn

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator
from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

from facebook_monitor.facebook.page_guard_script import (
    FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT,
)
from facebook_monitor.worker.facebook_page_guard import assess_sync_facebook_page


_FACEBOOK_GROUP_URL = "https://www.facebook.com/groups/111"
_CONTENT_TITLE_MARKERS = ("目前無法查看此內容",)
_CONTENT_DETAIL_MARKERS = ("變更了分享對象", "刪除了內容")
_BLOCK_TITLE_MARKERS = ("你暫時遭到封鎖",)
_BLOCK_DETAIL_MARKERS = ("你似乎過度使用了這項功能",)


def _skip_or_fail_chromium_launch(exc: PlaywrightError) -> NoReturn:
    """CI 必須啟動 Chromium；本機缺 browser 時保留可行動提示。"""

    if os.environ.get("CI"):
        pytest.fail(f"Chromium launch is required in CI: {exc}", pytrace=False)
    pytest.skip(
        "Chromium browser is unavailable; run "
        "`uv run python -m playwright install chromium`. "
        f"Launch error: {exc}"
    )


class _FacebookUrlPage:
    """委派真實 Playwright Page，但提供 Facebook URL 給純分類器。"""

    def __init__(self, page: Page) -> None:
        self._page = page
        self.url = _FACEBOOK_GROUP_URL

    def locator(self, selector: str) -> Locator:
        """委派 locator。"""

        return self._page.locator(selector)

    def evaluate(self, script: str, arg: object) -> Any:
        """在真實 Chromium document 執行 page guard script。"""

        return self._page.evaluate(script, arg)

    def wait_for_timeout(self, timeout: float) -> None:
        """委派 temporary-block 的穩定性等待。"""

        self._page.wait_for_timeout(timeout)


@pytest.fixture(scope="module")
def chromium_page() -> Iterator[Page]:
    """建立一次真實 Chromium page，供小型去識別 DOM cases 共用。"""

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, timeout=10_000)
        except PlaywrightError as exc:
            _skip_or_fail_chromium_launch(exc)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def test_chromium_launch_failure_fails_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI 的 Chromium launch failure 必須讓 safety contract 紅燈。"""

    monkeypatch.setenv("CI", "true")

    with pytest.raises(pytest.fail.Exception, match="Chromium launch is required in CI"):
        _skip_or_fail_chromium_launch(PlaywrightError("launch failed"))


def test_chromium_launch_failure_skips_outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """本機未安裝 Chromium 時可 skip，並提示正式安裝指令。"""

    monkeypatch.delenv("CI", raising=False)

    with pytest.raises(pytest.skip.Exception, match="playwright install chromium"):
        _skip_or_fail_chromium_launch(PlaywrightError("launch failed"))


@pytest.mark.parametrize(
    (
        "html",
        "title_markers",
        "detail_markers",
        "expected_inside_feed",
        "expects_visible_feed",
        "expected_local_relation",
        "expected_reason",
    ),
    [
        pytest.param(
            """
            <main role="main">
              <section>
                <h2>目前無法查看此內容</h2>
                <p>擁有者可能變更了分享對象，或是刪除了內容。</p>
              </section>
            </main>
            """,
            _CONTENT_TITLE_MARKERS,
            _CONTENT_DETAIL_MARKERS,
            False,
            False,
            True,
            "content_unavailable",
            id="full-page-content-unavailable",
        ),
        pytest.param(
            """
            <main role="main">
              <div role="feed">
                <div>
                  <a href="/groups/111/posts/222">仍可查看的貼文</a>
                  <h2>目前無法查看此內容</h2>
                  <p>嵌入內容的擁有者可能變更了分享對象。</p>
                </div>
              </div>
            </main>
            """,
            _CONTENT_TITLE_MARKERS,
            _CONTENT_DETAIL_MARKERS,
            True,
            True,
            True,
            None,
            id="content-unavailable-quoted-inside-feed",
        ),
        pytest.param(
            """
            <main role="main">
              <div role="feed">
                <div><a href="/groups/111/posts/222">背景仍可見的貼文</a></div>
              </div>
              <section role="dialog">
                <h2>你暫時遭到封鎖</h2>
                <p>你似乎過度使用了這項功能，因此暫時無法使用。</p>
              </section>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            False,
            True,
            True,
            "facebook_temporary_block",
            id="temporary-block-overlay-with-visible-feed",
        ),
        pytest.param(
            """
            <main role="main">
              <div class="common-layout-wrapper">
                <div role="feed">
                  <div><a href="/groups/111/posts/222">背景仍可見的貼文</a></div>
                </div>
                <section>
                  <h2>你暫時遭到封鎖</h2>
                  <p>你似乎過度使用了這項功能，因此暫時無法使用。</p>
                </section>
              </div>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            False,
            True,
            True,
            "facebook_temporary_block",
            id="temporary-block-generic-wrapper-with-visible-feed",
        ),
        pytest.param(
            """
            <main role="main">
              <div class="common-layout-wrapper">
                <div role="feed" hidden>
                  <div><a href="/groups/111/posts/222">隱藏的舊貼文</a></div>
                </div>
                <section>
                  <h2>你暫時遭到封鎖</h2>
                  <p>你似乎過度使用了這項功能，因此暫時無法使用。</p>
                </section>
              </div>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            False,
            False,
            True,
            "facebook_temporary_block",
            id="temporary-block-generic-wrapper-with-hidden-retained-feed",
        ),
        pytest.param(
            """
            <main role="main">
              <div role="feed">
                <div>
                  <a href="/groups/111/posts/222">使用者求助貼文</a>
                  <h2>你暫時遭到封鎖</h2>
                  <p>使用者表示：你似乎過度使用了這項功能。</p>
                </div>
              </div>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            True,
            True,
            True,
            None,
            id="temporary-block-quoted-inside-feed",
        ),
        pytest.param(
            """
            <main role="main">
              <section>
                <a href="/groups/111/posts/222">使用者求助貼文</a>
                <h2>你暫時遭到封鎖</h2>
                <p>使用者表示：你似乎過度使用了這項功能。</p>
              </section>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            True,
            True,
            True,
            None,
            id="temporary-block-quoted-in-permalink-only-container",
        ),
        pytest.param(
            """
            <main role="main">
              <section>
                <div><a href="/groups/111/posts/222">使用者求助貼文</a></div>
                <div role="feed"><div>貼文下方的留言 feed</div></div>
                <div>
                  <h2>你暫時遭到封鎖</h2>
                  <p>使用者表示：你似乎過度使用了這項功能。</p>
                </div>
              </section>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            True,
            True,
            True,
            None,
            id="temporary-block-quoted-with-nested-feed-outside-permalink",
        ),
        pytest.param(
            f"""
            <main role="main">
              <section>
                <h2>你暫時遭到封鎖</h2>
                <div>{"很長的非局部內容" * 400}</div>
                <p>你似乎過度使用了這項功能，因此暫時無法使用。</p>
              </section>
            </main>
            """,
            _BLOCK_TITLE_MARKERS,
            _BLOCK_DETAIL_MARKERS,
            False,
            False,
            False,
            "facebook_page_guard_inconclusive",
            id="temporary-block-markers-in-oversized-container",
        ),
    ],
)
def test_page_guard_browser_script_and_classifier_contract(
    chromium_page: Page,
    html: str,
    title_markers: tuple[str, ...],
    detail_markers: tuple[str, ...],
    expected_inside_feed: bool,
    expects_visible_feed: bool,
    expected_local_relation: bool,
    expected_reason: str | None,
) -> None:
    """真實執行 DOM probe，並驗證 marker context 會導向正確分類。"""

    chromium_page.set_content(html)
    payload = chromium_page.evaluate(
        FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT,
        {
            "titleMarkers": title_markers,
            "detailMarkers": detail_markers,
        },
    )

    assert isinstance(payload, dict)
    assert payload["matchedHeadingText"]
    assert payload["matchedDetailText"]
    assert payload["headingInsideFeed"] is expected_inside_feed
    assert payload["detailInsideFeed"] is expected_inside_feed
    assert payload["headingDetailLocal"] is expected_local_relation
    visible_count = payload["visibleFeedCandidateCount"]
    assert isinstance(visible_count, int)
    assert (visible_count > 0) is expects_visible_feed

    finding = assess_sync_facebook_page(_FacebookUrlPage(chromium_page))

    assert (finding.reason if finding else None) == expected_reason
