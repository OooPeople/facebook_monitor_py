"""Debug capture script tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.facebook.route_detection import RouteDetectionError
from scripts.debug.capture_posts_target import BrowserPageSnapshot
from scripts.debug.capture_posts_target import select_capture_route


def test_select_capture_route_uses_valid_group_tab() -> None:
    """起始 tab 停在 groups feed 時，仍可選到其他有效 group tab。"""

    selection = select_capture_route(
        [
            BrowserPageSnapshot(
                page_index=1,
                url="https://www.facebook.com/groups/feed/",
                title="社團動態",
            ),
            BrowserPageSnapshot(
                page_index=2,
                url="https://www.facebook.com/groups/222518561920110",
                title="測試社團 | Facebook",
            ),
        ]
    )

    assert selection.route.group_id == "222518561920110"
    assert selection.snapshot.page_index == 2
    assert selection.source_url == "https://www.facebook.com/groups/222518561920110"


def test_select_capture_route_uses_dom_candidate_url() -> None:
    """page.url 未同步時可用 DOM 候選 URL 辨識目前社團。"""

    selection = select_capture_route(
        [
            BrowserPageSnapshot(
                page_index=1,
                url="https://www.facebook.com/groups/feed/",
                title="測試社團 | Facebook",
                candidate_urls=("https://www.facebook.com/groups/222518561920110",),
            )
        ]
    )

    assert selection.route.group_id == "222518561920110"
    assert selection.source_url == "https://www.facebook.com/groups/222518561920110"
    assert selection.valid_count == 1


def test_select_capture_route_dedupes_same_group_on_same_page() -> None:
    """同一頁同一社團的多個 URL 候選只算一次。"""

    selection = select_capture_route(
        [
            BrowserPageSnapshot(
                page_index=1,
                url="https://www.facebook.com/groups/222518561920110/",
                title="測試社團 | Facebook",
                candidate_urls=("https://www.facebook.com/groups/222518561920110",),
            )
        ]
    )

    assert selection.route.group_id == "222518561920110"
    assert selection.valid_count == 1


def test_select_capture_route_reports_seen_pages_when_no_valid_group() -> None:
    """沒有有效 group tab 時，錯誤訊息包含 Playwright 看得到的 URL。"""

    with pytest.raises(RouteDetectionError) as exc_info:
        select_capture_route(
            [
                BrowserPageSnapshot(
                    page_index=1,
                    url="https://www.facebook.com/groups/feed/",
                    title="社團動態",
                )
            ]
        )

    error_message = str(exc_info.value)
    assert "https://www.facebook.com/groups/feed/" in error_message
    assert "candidates=" in error_message
