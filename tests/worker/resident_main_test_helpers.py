"""Shared fakes for resident main worker tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Error as AsyncPlaywrightError
from pytest import MonkeyPatch

from facebook_monitor.scheduler.planner import DueTarget
from facebook_monitor.scheduler.planner import TargetSchedulePlanner


class FakeAsyncLocator:
    """resident scan 測試用 locator。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳可掃描頁面的 body text。"""

        return "Facebook group page"


class FakeAsyncPage:
    """測試用 async page，記錄導航與關閉狀態。"""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto_count = 0
        self.reload_count = 0
        self.closed = False

    async def goto(self, url: str, wait_until: str, timeout: float) -> None:
        """模擬 async 導航。"""

        self.url = url.rstrip("/")
        self.goto_count += 1

    async def reload(self, wait_until: str, timeout: float) -> None:
        """模擬 async reload。"""

        self.reload_count += 1

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬 Playwright 等待。"""

    def locator(self, selector: str) -> FakeAsyncLocator:
        """回傳 body locator。"""

        return FakeAsyncLocator()

    def is_closed(self) -> bool:
        """回傳 page 是否關閉。"""

        return self.closed

    async def close(self) -> None:
        """標記 page 已關閉。"""

        self.closed = True


class FakeAsyncBrowserContext:
    """測試用 async browser context。"""

    def __init__(self) -> None:
        self.pages: list[FakeAsyncPage] = []
        self.closed = False
        self.default_timeout = 0.0
        self.default_navigation_timeout = 0.0

    def set_default_timeout(self, timeout: float) -> None:
        """記錄 context default timeout。"""

        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """記錄 context default navigation timeout。"""

        self.default_navigation_timeout = timeout

    async def new_page(self) -> FakeAsyncPage:
        """建立 fake async page。"""

        page = FakeAsyncPage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        """標記 browser context 已關閉。"""

        self.closed = True


class FakeMetadataLocator:
    """metadata refresh 測試用 locator。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳已登入狀態的 body text。"""

        return "Facebook group page"


class FakeLoggedOutMetadataLocator(FakeMetadataLocator):
    """metadata refresh 失敗測試用 locator。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳未登入頁面的 body text。"""

        return "Log into Facebook"


class FakeMetadataPage:
    """metadata refresh 測試用 page。"""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.closed = False

    async def goto(
        self,
        url: str,
        wait_until: str,
        timeout: float | None = None,
    ) -> None:
        """記錄 metadata refresh 導航 URL。"""

        self.url = url

    async def reload(self, wait_until: str, timeout: float) -> None:
        """模擬 resident scan page reload。"""

    def is_closed(self) -> bool:
        """回傳 page 是否已關閉。"""

        return self.closed

    async def wait_for_timeout(self, milliseconds: int) -> None:
        """模擬等待頁面 title 更新。"""

    def locator(self, selector: str) -> FakeMetadataLocator:
        """回傳 body locator。"""

        return FakeMetadataLocator()

    async def title(self) -> str:
        """回傳可清理的 Facebook title。"""

        return "(20+) 測試社團 | Facebook"

    async def evaluate(self, script: str) -> str:
        """回傳 metadata resolver 抽到的 cover image URL。"""

        return "https://scontent.xx.fbcdn.net/group-cover.jpg"

    async def close(self) -> None:
        """標記 metadata page 已關閉。"""

        self.closed = True


class FakeLoggedOutMetadataPage(FakeMetadataPage):
    """metadata refresh 失敗測試用 page。"""

    def locator(self, selector: str) -> FakeLoggedOutMetadataLocator:
        """回傳未登入頁面的 body locator。"""

        return FakeLoggedOutMetadataLocator()


class FakeMetadataBrowserContext:
    """metadata refresh 測試用 browser context。"""

    def __init__(self) -> None:
        self.pages: list[FakeMetadataPage] = []

    async def new_page(self) -> FakeMetadataPage:
        """建立 metadata page。"""

        page = FakeMetadataPage()
        self.pages.append(page)
        return page


class RuntimeRefreshMetadataBrowserContext(FakeMetadataBrowserContext):
    """可作為 resident persistent context 的 metadata 測試 context。"""

    def __init__(self, *, fail_new_page: bool = False) -> None:
        super().__init__()
        self.fail_new_page = fail_new_page
        self.closed = False
        self.default_timeout = 0.0
        self.default_navigation_timeout = 0.0

    def set_default_timeout(self, timeout: float) -> None:
        """記錄 context default timeout。"""

        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: float) -> None:
        """記錄 context default navigation timeout。"""

        self.default_navigation_timeout = timeout

    async def new_page(self) -> FakeMetadataPage:
        """建立 metadata page；必要時模擬 browser runtime 已中斷。"""

        if self.fail_new_page:
            raise AsyncPlaywrightError("Connection closed while reading from the driver")
        return await super().new_page()

    async def close(self) -> None:
        """標記 browser context 已關閉。"""

        self.closed = True


class RuntimeClosedOnPausedPage(FakeMetadataPage):
    """只有進入 paused target URL 時才模擬 browser runtime closed。"""

    async def goto(
        self,
        url: str,
        wait_until: str,
        timeout: float | None = None,
    ) -> None:
        """paused maintenance 若未被 filter 會在此觸發 runtime failure。"""

        if "groups/paused" in url:
            raise AsyncPlaywrightError("Connection closed while reading from the driver")
        await super().goto(url, wait_until=wait_until, timeout=timeout)


class RuntimeClosedOnPausedBrowserContext(RuntimeRefreshMetadataBrowserContext):
    """paused maintenance starvation 測試用 browser context。"""

    async def new_page(self) -> RuntimeClosedOnPausedPage:
        """建立只在 paused target 導航時失敗的 page。"""

        page = RuntimeClosedOnPausedPage()
        self.pages.append(page)
        return page


class FakeLoggedOutMetadataBrowserContext:
    """metadata refresh 失敗測試用 browser context。"""

    def __init__(self) -> None:
        self.pages: list[FakeLoggedOutMetadataPage] = []

    async def new_page(self) -> FakeLoggedOutMetadataPage:
        """建立未登入 fake metadata page。"""

        page = FakeLoggedOutMetadataPage()
        self.pages.append(page)
        return page


class FakeShutdownMetadataBrowserContext:
    """模擬 scheduler 停止時 Playwright driver 已關閉的 browser context。"""

    def __init__(self, on_new_page: Any) -> None:
        self.on_new_page = on_new_page
        self.new_page_count = 0

    async def new_page(self) -> FakeMetadataPage:
        """在開頁時切換 stop 狀態並丟出 Playwright shutdown 例外。"""

        self.new_page_count += 1
        self.on_new_page()
        raise Exception("BrowserContext.new_page: Connection closed while reading from the driver")


class RecordingSchedulePlanner(TargetSchedulePlanner):
    """記錄 dispatch 時機，避免 async resident 在 queue 階段推進 next_due_at。"""

    def __init__(self) -> None:
        super().__init__()
        self.dispatched_target_ids: list[str] = []

    def mark_dispatched(self, due_target: DueTarget, *, now: Any = None) -> None:
        self.dispatched_target_ids.append(due_target.target_id)
        super().mark_dispatched(due_target, now=now)


def _stub_runtime_outbox_dispatch(monkeypatch: MonkeyPatch) -> list[Path]:
    """避免 runtime failure 通知測試觸發外部 I/O，並記錄 dispatch DB。"""

    dispatch_calls: list[Path] = []

    def fake_dispatch(**kwargs: object) -> int:
        db_path = kwargs["db_path"]
        assert isinstance(db_path, Path)
        dispatch_calls.append(db_path)
        return 0

    monkeypatch.setattr(
        "facebook_monitor.notifications.outbox_service.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )
    return dispatch_calls
