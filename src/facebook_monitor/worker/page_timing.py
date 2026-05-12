"""Worker page preparation timing constants。

職責：集中正式 resident 與 fallback/debug worker 共用的頁面進入等待時間。
"""

RESIDENT_PAGE_READY_WAIT_MS = 5000


__all__ = ["RESIDENT_PAGE_READY_WAIT_MS"]
