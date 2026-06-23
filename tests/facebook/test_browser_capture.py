"""Browser capture helper tests。"""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from facebook_monitor.facebook.browser_capture import BrowserPageSnapshot
from facebook_monitor.facebook.browser_capture import extract_page_candidate_urls
from facebook_monitor.facebook.browser_capture import get_start_page
from facebook_monitor.facebook.browser_capture import select_capture_route
from facebook_monitor.facebook.browser_capture import snapshot_browser_pages


def test_snapshot_browser_pages_skips_closed_pages_and_tolerates_page_errors() -> None:
    """capture snapshot 應略過 closed pages，且 title/evaluate 失敗時保留可診斷 URL。"""

    snapshots = snapshot_browser_pages(
        [
            _FakeCapturePage("https://www.facebook.com/groups/closed", closed=True),
            _FakeCapturePage(
                "https://www.facebook.com/groups/222518561920110",
                title_error=RuntimeError("title failed"),
                evaluate_error=RuntimeError("evaluate failed"),
            ),
        ]
    )

    assert snapshots == [
        BrowserPageSnapshot(
            page_index=2,
            url="https://www.facebook.com/groups/222518561920110",
            title="",
            candidate_urls=(),
        )
    ]


def test_extract_page_candidate_urls_filters_empty_values() -> None:
    """DOM candidate payload 會去除空值並轉成 tuple，供 route detection 使用。"""

    page = _FakeCapturePage(
        "https://www.facebook.com/groups/222518561920110",
        candidate_payload=[
            "",
            None,
            "https://www.facebook.com/groups/222518561920110?sorting_setting=CHRONOLOGICAL",
        ],
    )

    assert extract_page_candidate_urls(page) == (
        "https://www.facebook.com/groups/222518561920110?sorting_setting=CHRONOLOGICAL",
    )


def test_extract_page_candidate_urls_reads_dom_route_hints() -> None:
    """內嵌 DOM script 需讀取 canonical、og:url 與 active group tab 候選。"""

    html = """
    <html>
      <head>
        <link rel="canonical" href="https://www.facebook.com/groups/111111111111111">
        <meta property="og:url" content="https://www.facebook.com/groups/222222222222222">
      </head>
      <body>
        <a aria-current="page" href="https://www.facebook.com/groups/333333333333333">
          Group tab
        </a>
      </body>
    </html>
    """

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, timeout=10_000)
        except PlaywrightError as exc:
            pytest.skip(f"chromium browser is not installed: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            candidates = extract_page_candidate_urls(page)
        finally:
            browser.close()

    assert "https://www.facebook.com/groups/111111111111111" in candidates
    assert "https://www.facebook.com/groups/222222222222222" in candidates
    assert "https://www.facebook.com/groups/333333333333333" in candidates


def test_select_capture_route_uses_last_valid_group_candidate() -> None:
    """多個可用 group tab 時，capture 會選最後一個有效候選並回報 valid_count。"""

    selected = select_capture_route(
        [
            BrowserPageSnapshot(
                page_index=1,
                url="https://www.facebook.com/groups/111111111111111",
                title="First Group | Facebook",
            ),
            BrowserPageSnapshot(
                page_index=2,
                url="https://www.facebook.com/",
                title="Second Group | Facebook",
                candidate_urls=(
                    "https://www.facebook.com/groups/222222222222222?ref=bookmarks",
                ),
            ),
        ]
    )

    assert selected.route.group_id == "222222222222222"
    assert selected.snapshot.page_index == 2
    assert selected.source_url == "https://www.facebook.com/groups/222222222222222?ref=bookmarks"
    assert selected.valid_count == 2


def test_get_start_page_reuses_first_open_page_or_creates_one() -> None:
    """profile/login flow 會重用第一個未關閉 page；全關閉時才 new_page。"""

    open_page = _FakeCapturePage("https://www.facebook.com/groups/222518561920110")
    context = _FakeCaptureContext(
        [
            _FakeCapturePage("about:blank", closed=True),
            open_page,
        ]
    )

    assert get_start_page(context) is open_page

    empty_context = _FakeCaptureContext([_FakeCapturePage("about:blank", closed=True)])
    created_page = get_start_page(empty_context)

    assert created_page is empty_context.created_page


class _FakeCapturePage:
    """測試用 capture page。"""

    def __init__(
        self,
        url: str,
        *,
        closed: bool = False,
        title: str = "Group | Facebook",
        title_error: Exception | None = None,
        evaluate_error: Exception | None = None,
        candidate_payload: list[object] | None = None,
    ) -> None:
        self.url = url
        self._closed = closed
        self._title = title
        self._title_error = title_error
        self._evaluate_error = evaluate_error
        self._candidate_payload: list[object] = (
            candidate_payload if candidate_payload is not None else [url]
        )

    def is_closed(self) -> bool:
        """回傳 page 是否已關閉。"""

        return self._closed

    def title(self) -> str:
        """回傳 page title 或模擬 Playwright 讀取錯誤。"""

        if self._title_error is not None:
            raise self._title_error
        return self._title

    def evaluate(self, _script: str) -> list[object]:
        """回傳 DOM candidate payload 或模擬 evaluate 錯誤。"""

        if self._evaluate_error is not None:
            raise self._evaluate_error
        return self._candidate_payload


class _FakeCaptureContext:
    """測試用 capture context。"""

    def __init__(self, pages: list[_FakeCapturePage]) -> None:
        self.pages = pages
        self.created_page: _FakeCapturePage | None = None

    def new_page(self) -> _FakeCapturePage:
        """建立新的 fake page。"""

        self.created_page = _FakeCapturePage("about:blank")
        return self.created_page
