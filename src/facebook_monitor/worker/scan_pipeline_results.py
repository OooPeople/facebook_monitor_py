"""Side-effect-free scanner pipeline result models.

職責：承載 scanner 在不直接寫 DB 時交給 resident executor / coordinator
處理的最小結果；本模組不 import Playwright 或 SQLite。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Mapping
from typing import TypeAlias

from facebook_monitor.worker.scan_finalize import NormalizedScanItem


@dataclass(frozen=True)
class SuccessScanResult:
    """表示 scanner 已完成抽取，等待 coordinator 寫入 success scan state。"""

    target_id: str
    url: str
    items: tuple[NormalizedScanItem, ...]
    item_count: int
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ProtectiveSkipScanResult:
    """表示 scanner 判定本輪需保護性跳過，但尚未寫入 scan state。"""

    target_id: str
    url: str
    metadata: Mapping[str, Any]

    @property
    def skip_reason(self) -> str:
        """回傳 protective skip reason，供 outcome reason 對齊使用。"""

        return str(self.metadata.get("skip_reason") or "")


FormalAsyncScanResult: TypeAlias = SuccessScanResult | ProtectiveSkipScanResult
"""正式 async resident scanner 可回傳的 commit-ready result。"""


__all__ = [
    "FormalAsyncScanResult",
    "ProtectiveSkipScanResult",
    "SuccessScanResult",
]
