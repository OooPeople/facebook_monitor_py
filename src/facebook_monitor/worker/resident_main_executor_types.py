"""Shared resident executor protocol and result types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AsyncResidentPageLike(Protocol):
    """resident executor page preparation 需要的 async Playwright page 能力。"""

    url: str

    async def reload(self, *, wait_until: str, timeout: float) -> object:
        """重新載入目前 page。"""

    async def goto(self, url: str, *, wait_until: str, timeout: float) -> object:
        """前往指定 URL。"""

    async def wait_for_timeout(self, timeout: int) -> None:
        """等待指定毫秒。"""


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
