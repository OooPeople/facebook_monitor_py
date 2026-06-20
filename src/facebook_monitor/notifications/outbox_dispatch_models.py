"""Notification outbox dispatch 的輕量資料模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingNotificationOutboxDispatchResult:
    """描述一次 pending outbox drain 的成功數與停止原因。"""

    dispatched_count: int
    claimed_count: int
    batch_count: int
    reached_batch_limit: bool = False
    stopped: bool = False

    @property
    def should_continue(self) -> bool:
        """本輪因 bounded batch 停下時，dispatcher 應排下一輪檢查 backlog。"""

        return self.claimed_count > 0 and self.reached_batch_limit and not self.stopped


__all__ = ["PendingNotificationOutboxDispatchResult"]
