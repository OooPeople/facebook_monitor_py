"""Worker page preparation timing constants。

職責：集中正式 resident 與 fallback/debug worker 共用的頁面進入等待時間。
"""

RESIDENT_PAGE_READY_WAIT_MS = 5000
FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS = 250


__all__ = [
    "FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS",
    "RESIDENT_PAGE_READY_WAIT_MS",
]
