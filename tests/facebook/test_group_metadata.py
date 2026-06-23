"""Facebook group metadata resolver tests。"""

from __future__ import annotations

import asyncio

import pytest

from facebook_monitor.facebook.group_metadata import GroupMetadataError
from facebook_monitor.facebook.group_metadata import resolve_group_cover_image_with_context
from facebook_monitor.facebook.group_metadata import resolve_group_metadata_with_context


CANONICAL_URL = "https://www.facebook.com/groups/222518561920110"


def test_resolve_group_metadata_rejects_wrong_final_group_and_closes_page() -> None:
    """metadata resolver 不可接受 Facebook 導向其他 group 的結果。"""

    page = _FakeMetadataPage(
        final_url="https://www.facebook.com/groups/999999999999999",
        title="Other Group | Facebook",
        body_text="group page",
        cover_url="https://scontent.xx.fbcdn.net/v/t39.30808-6/cover.jpg",
    )
    context = _FakeMetadataContext(page)

    with pytest.raises(GroupMetadataError, match="導向非目標社團"):
        asyncio.run(
            resolve_group_metadata_with_context(
                context,
                canonical_url=CANONICAL_URL,
                wait_ms=0,
            )
        )

    assert page.closed


def test_resolve_group_metadata_rejects_chinese_logged_out_body() -> None:
    """中文登入頁 body 需被辨識為未登入，而不是寫入錯誤 metadata。"""

    page = _FakeMetadataPage(
        final_url=CANONICAL_URL,
        title="Facebook",
        body_text="請先登入 Facebook 才能繼續",
        cover_url="https://scontent.xx.fbcdn.net/v/t39.30808-6/cover.jpg",
    )

    with pytest.raises(GroupMetadataError, match="Facebook 尚未登入"):
        asyncio.run(
            resolve_group_metadata_with_context(
                _FakeMetadataContext(page),
                canonical_url=CANONICAL_URL,
                wait_ms=0,
            )
        )

    assert page.closed


def test_resolve_group_metadata_drops_generic_cover_image_url() -> None:
    """metadata resolver 有接上 cover image sanitizer，不輸出 Facebook 通用圖。"""

    page = _FakeMetadataPage(
        final_url=CANONICAL_URL,
        title="Test Group | Facebook",
        body_text="group page",
        cover_url="https://static.facebook.com/images/logos/facebook_2x.png",
    )

    metadata = asyncio.run(
        resolve_group_metadata_with_context(
            _FakeMetadataContext(page),
            canonical_url=CANONICAL_URL,
            wait_ms=0,
        )
    )

    assert metadata.group_name == "Test Group"
    assert metadata.group_cover_image_url == ""
    assert page.closed


def test_resolve_group_cover_image_does_not_require_group_name() -> None:
    """cover-only refresh 不需要解析 group name，但仍需回傳安全 cover URL。"""

    cover_url = "https://scontent.xx.fbcdn.net/v/t39.30808-6/cover.jpg"
    page = _FakeMetadataPage(
        final_url=CANONICAL_URL,
        title="",
        body_text="group page",
        cover_url=cover_url,
    )

    result = asyncio.run(
        resolve_group_cover_image_with_context(
            _FakeMetadataContext(page),
            canonical_url=CANONICAL_URL,
            wait_ms=0,
        )
    )

    assert result == cover_url
    assert page.closed


class _FakeMetadataLocator:
    """測試用 locator。"""

    def __init__(self, text: str) -> None:
        self.text = text

    async def inner_text(self, *, timeout: int) -> str:
        """回傳測試 body text。"""

        assert timeout == 10000
        return self.text


class _FakeMetadataPage:
    """測試用 async metadata page。"""

    def __init__(
        self,
        *,
        final_url: str,
        title: str,
        body_text: str,
        cover_url: str,
    ) -> None:
        self.url = final_url
        self._title = title
        self._body_text = body_text
        self._cover_url = cover_url
        self.goto_calls: list[tuple[str, str]] = []
        self.wait_calls: list[int] = []
        self.closed = False

    async def goto(self, url: str, *, wait_until: str) -> object:
        """記錄導頁。"""

        self.goto_calls.append((url, wait_until))
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        """記錄等待時間。"""

        self.wait_calls.append(timeout)

    def locator(self, selector: str) -> _FakeMetadataLocator:
        """回傳 fake body locator。"""

        assert selector == "body"
        return _FakeMetadataLocator(self._body_text)

    async def title(self) -> str:
        """回傳測試頁面標題。"""

        return self._title

    async def evaluate(self, _script: str) -> str:
        """回傳測試 cover URL。"""

        return self._cover_url

    async def close(self) -> None:
        """標記 page 已關閉。"""

        self.closed = True


class _FakeMetadataContext:
    """測試用 async browser context。"""

    def __init__(self, page: _FakeMetadataPage) -> None:
        self.page = page

    async def new_page(self) -> _FakeMetadataPage:
        """回傳 fake page。"""

        return self.page
