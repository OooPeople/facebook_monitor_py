"""Target runtime decision models。

職責：保存 runtime transition 會共用的輕量 decision 型別，避免 service 與
transition builder 互相 import。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScanSkipDecision:
    """描述一次保護性 skipped scan 是否需要升級成 recoverable failure。"""

    reason: str
    skip_streak: int
    skip_limit: int

    @property
    def escalate(self) -> bool:
        """回傳本次 skip 是否已達升級門檻。"""

        return self.skip_streak >= self.skip_limit


__all__ = ["ScanSkipDecision"]
