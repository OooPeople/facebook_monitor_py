"""Process-local Facebook access trip latch 與 visible-write fence。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock

from facebook_monitor.core.facebook_access import FacebookAdmissionToken


@dataclass(frozen=True)
class FacebookAccessRuntimeGateSnapshot:
    """提供 privacy-safe runtime gate diagnostics。"""

    trip_requested: bool
    writes_closed: bool
    safety_epoch: int
    browser_io_poisoned: bool = False


class FacebookAccessRuntimeGate:
    """在 persistent DB transition 前先同步關閉 process visible writes。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._trip_requested = False
        self._writes_closed = False
        self._safety_epoch = 0
        self._browser_io_poisoned = False

    def snapshot(self) -> FacebookAccessRuntimeGateSnapshot:
        """讀取目前 process latch 狀態。"""

        with self._lock:
            return FacebookAccessRuntimeGateSnapshot(
                trip_requested=self._trip_requested,
                writes_closed=self._writes_closed,
                safety_epoch=self._safety_epoch,
                browser_io_poisoned=self._browser_io_poisoned,
            )

    def poison_browser_io(self) -> None:
        """將未確認收旂的 browser runtime 永久隔離到 process 重建。"""

        with self._lock:
            if not self._browser_io_poisoned:
                self._safety_epoch += 1
            self._browser_io_poisoned = True
            self._writes_closed = True

    def browser_io_is_poisoned(self) -> bool:
        """回傳本 process 是否已禁止任何新 Facebook browser I/O。"""

        with self._lock:
            return self._browser_io_poisoned

    def current_safety_epoch(self) -> int:
        """回傳建立 DB admission token 時應綁定的 epoch。"""

        with self._lock:
            return self._safety_epoch

    def request_trip(self, token: FacebookAdmissionToken) -> bool:
        """原子關閉 writes 並推進 epoch；stale token 不得重開 episode。"""

        with self._lock:
            if token.process_safety_epoch != self._safety_epoch:
                return False
            self._trip_requested = True
            self._writes_closed = True
            self._safety_epoch += 1
            return True

    def admission_is_process_current(self, token: FacebookAdmissionToken) -> bool:
        """visible write 前檢查 token 沒被 trip latch 淘汰。"""

        with self._lock:
            return bool(
                not self._writes_closed
                and not self._trip_requested
                and token.process_safety_epoch == self._safety_epoch
            )

    @contextmanager
    def normal_visible_write_fence(
        self,
        token: FacebookAdmissionToken,
    ) -> Iterator[bool]:
        """鎖住 normal visible commit 與 trip linearization 的先後關係。"""

        self._lock.acquire()
        try:
            yield bool(
                not self._writes_closed
                and not self._trip_requested
                and token.process_safety_epoch == self._safety_epoch
            )
        finally:
            self._lock.release()

    def reset_after_verified_closed_circuit(self) -> int:
        """只供已確認 DB circuit closed 的 supervisor 解除非 fatal latch。"""

        with self._lock:
            self._safety_epoch += 1
            self._trip_requested = False
            self._writes_closed = self._browser_io_poisoned
            return self._safety_epoch


__all__ = [
    "FacebookAccessRuntimeGate",
    "FacebookAccessRuntimeGateSnapshot",
]
