"""Resident target queue。

職責：提供 resident main executor 使用的去重 queue，確保同一 target
不會同時處於 queued / running。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from facebook_monitor.scheduler.planner import DueTarget


@dataclass(frozen=True)
class QueueItem:
    """保存 executor queue 內的一筆 target scan admission。"""

    due_target: DueTarget
    enqueue_reason: str
    enqueued_at: datetime


class TargetQueue:
    """去重 target queue，確保同一 target 不會同時 queued / running。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueItem | None] = asyncio.Queue()
        self._queued_target_ids: set[str] = set()
        self._queued_order: list[str] = []
        self._running_target_owners: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, item: QueueItem) -> bool:
        """將 target 放入 queue；若已 queued/running 則拒絕重複 enqueue。"""

        target_id = item.due_target.target_id
        async with self._lock:
            if target_id in self._queued_target_ids or target_id in self._running_target_owners:
                return False
            self._queued_target_ids.add(target_id)
            self._queued_order.append(target_id)
            await self._queue.put(item)
            return True

    async def reserve(self, item: QueueItem) -> bool:
        """先保留 target queue 位置，但暫不交給 worker 消費。"""

        target_id = item.due_target.target_id
        async with self._lock:
            if target_id in self._queued_target_ids or target_id in self._running_target_owners:
                return False
            self._queued_target_ids.add(target_id)
            self._queued_order.append(target_id)
            return True

    async def publish_reserved(self, item: QueueItem) -> bool:
        """將已保留的 queue item 發布給 worker 消費。"""

        target_id = item.due_target.target_id
        async with self._lock:
            if target_id not in self._queued_target_ids:
                return False
            await self._queue.put(item)
            return True

    async def release_reserved(self, target_id: str) -> None:
        """釋放尚未發布給 worker 的保留 queue 位置。"""

        async with self._lock:
            if target_id in self._running_target_owners:
                return
            self._queued_target_ids.discard(target_id)
            try:
                self._queued_order.remove(target_id)
            except ValueError:
                pass

    async def get(self) -> QueueItem | None:
        """取得下一筆 queue item，並把 target 從 queued 轉入 running set。"""

        item = await self._queue.get()
        if item is None:
            self._queue.task_done()
            return None
        target_id = item.due_target.target_id
        async with self._lock:
            self._queued_target_ids.discard(target_id)
            try:
                self._queued_order.remove(target_id)
            except ValueError:
                pass
            self._running_target_owners[target_id] = ""
        return item

    async def bind_running_owner(self, target_id: str, owner_key: str) -> None:
        """為已取出的 running target 綁定 attempt owner token。"""

        async with self._lock:
            if target_id in self._running_target_owners:
                self._running_target_owners[target_id] = owner_key

    async def release_running_if_owner(self, target_id: str, owner_key: str) -> bool:
        """只在 owner token 相符時釋放 running target；不呼叫 task_done。"""

        async with self._lock:
            current_owner = self._running_target_owners.get(target_id)
            if current_owner != owner_key:
                return False
            self._running_target_owners.pop(target_id, None)
            return True

    async def complete(self, target_id: str, owner_key: str = "") -> None:
        """標記 target worker 已結束，並通知 queue task done。"""

        async with self._lock:
            current_owner = self._running_target_owners.get(target_id)
            if (owner_key and current_owner == owner_key) or (
                not owner_key and current_owner == ""
            ):
                self._running_target_owners.pop(target_id, None)
        self._queue.task_done()

    async def stop_worker(self) -> None:
        """送出 worker 停止訊號。"""

        await self._queue.put(None)

    async def cancel_pending(self) -> tuple[str, ...]:
        """移除尚未被 worker 取出的 queue items，回傳取消的 target ids。"""

        cancelled_ids: list[str] = []
        async with self._lock:
            while True:
                try:
                    item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._queue.task_done()
                if item is None:
                    continue
                target_id = item.due_target.target_id
                self._queued_target_ids.discard(target_id)
                cancelled_ids.append(target_id)
            self._queued_order = [
                target_id for target_id in self._queued_order if target_id not in set(cancelled_ids)
            ]
        return tuple(cancelled_ids)

    async def join(self) -> None:
        """等待所有已排入 queue 的 target 完成。"""

        await self._queue.join()

    async def snapshot(self) -> tuple[int, int, tuple[str, ...]]:
        """回傳 queued/running 診斷資料。"""

        async with self._lock:
            queued_ids = tuple(self._queued_order)
            running_count = len(self._running_target_owners)
        return len(queued_ids), running_count, queued_ids
