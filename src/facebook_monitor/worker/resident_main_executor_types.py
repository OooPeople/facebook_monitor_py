"""Shared resident executor protocol and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.worker.scan_finalize import ScanCommitGuard
from facebook_monitor.worker.scan_orchestration import AsyncScannablePageLike
from facebook_monitor.worker.scan_pipeline_results import FormalAsyncScanResult


class AsyncResidentPageLike(Protocol):
    """resident executor page preparation 需要的 async Playwright page 能力。"""

    url: str

    async def reload(self, *, wait_until: str, timeout: float) -> object:
        """重新載入目前 page。"""

    async def goto(self, url: str, *, wait_until: str, timeout: float) -> object:
        """前往指定 URL。"""

    async def wait_for_timeout(self, timeout: int) -> None:
        """等待指定毫秒。"""


class AsyncReusablePageLike(AsyncResidentPageLike, AsyncScannablePageLike, Protocol):
    """resident page pool 需要的可重用 async Playwright page 能力。"""

    def is_closed(self) -> bool:
        """回傳 page 是否已關閉。"""

    async def close(self) -> None:
        """關閉 page。"""


class AsyncPagePoolBrowserContextLike(Protocol):
    """resident page pool 需要的 browser context 能力。"""

    async def new_page(self) -> AsyncReusablePageLike:
        """建立新的 async page。"""


class AsyncScanCallable(Protocol):
    """formal async resident 注入的 commit-ready target scan callable。"""

    async def __call__(
        self,
        *,
        page: AsyncReusablePageLike,
        app: ApplicationContext,
        target: TargetDescriptor,
        config: TargetConfig,
        scroll_rounds: int,
        scroll_wait_ms: int,
        commit_guard: ScanCommitGuard | None = None,
    ) -> FormalAsyncScanResult:
        """掃描已準備好的 target page，並回傳 coordinator commit-ready result。"""


@dataclass(frozen=True)
class AsyncTargetScanResult:
    """保存單一 target async scan 執行結果，供 diagnostics 彙整。"""

    target_id: str
    success: bool = False
    failure: bool = False
    skipped: bool = False
    opened_page: bool = False
    reused_page: bool = False


@dataclass(frozen=True)
class ExecutorCounters:
    """保存 worker pool 自上次讀取後累積的執行結果。"""

    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    opened_page_count: int = 0
    reused_page_count: int = 0
