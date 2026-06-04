"""Updater operation lock。

職責：限制 settings 更新流程同一時間只能有一個下載 / handoff / launch 操作，
避免重複點擊或多分頁並行時互相覆寫 pending update 與更新檔狀態。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import json
import os
from pathlib import Path
import threading
from typing import BinaryIO

if os.name == "nt":
    import msvcrt as _msvcrt_module

    _msvcrt: object | None = _msvcrt_module
    _fcntl: object | None = None
else:
    import fcntl as _fcntl_module

    _msvcrt = None
    _fcntl = _fcntl_module


UPDATE_OPERATION_LOCK_FILE_NAME = "update-operation.lock"
UPDATE_OPERATION_LOCK_BUSY_MESSAGE = "更新流程正在執行中，請稍後再試。"
_LOCK_BYTE_COUNT = 1
_ACTIVE_UPDATE_OPERATION_LOCKS: set[str] = set()
_ACTIVE_UPDATE_OPERATION_LOCKS_GUARD = threading.Lock()


class UpdateOperationLockError(RuntimeError):
    """表示目前已有另一個更新流程持有 operation lock。"""

    def __init__(self, message: str, *, lock_path: Path) -> None:
        super().__init__(message)
        self.lock_path = lock_path


@dataclass(frozen=True)
class UpdateOperationLock:
    """保存目前持有的 updater operation lock。"""

    runtime_dir: Path
    lock_path: Path
    owner: str


@dataclass(frozen=True)
class _UpdateOperationOwnerInfo:
    """寫入 lock 檔的持有者資訊，供排查重複更新操作。"""

    pid: int
    owner: str
    started_at: str


@contextmanager
def acquire_update_operation_lock(
    runtime_dir: Path,
    owner: str,
) -> Iterator[UpdateOperationLock]:
    """取得更新操作互斥鎖，離開 context 時自動釋放。"""

    resolved_runtime_dir = runtime_dir.expanduser().resolve()
    resolved_runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_runtime_dir / UPDATE_OPERATION_LOCK_FILE_NAME
    lock_identity = _lock_path_identity(lock_path)
    with _ACTIVE_UPDATE_OPERATION_LOCKS_GUARD:
        if lock_identity in _ACTIVE_UPDATE_OPERATION_LOCKS:
            raise UpdateOperationLockError(
                UPDATE_OPERATION_LOCK_BUSY_MESSAGE,
                lock_path=lock_path,
            )
        _ACTIVE_UPDATE_OPERATION_LOCKS.add(lock_identity)
    lock_file: BinaryIO | None = None
    locked = False
    try:
        lock_file = lock_path.open("a+b")
        _lock_file(lock_file, lock_path)
        locked = True
        _write_update_operation_owner_info(lock_file, owner=owner)
        yield UpdateOperationLock(
            runtime_dir=resolved_runtime_dir,
            lock_path=lock_path,
            owner=owner,
        )
    finally:
        with _ACTIVE_UPDATE_OPERATION_LOCKS_GUARD:
            _ACTIVE_UPDATE_OPERATION_LOCKS.discard(lock_identity)
        if lock_file is not None:
            if locked:
                _unlock_file(lock_file)
            lock_file.close()


def _lock_path_identity(path: Path) -> str:
    """回傳同 process re-entrant guard 使用的 canonical lock path。"""

    identity = str(path.expanduser().resolve())
    if os.name == "nt":
        return os.path.normcase(identity)
    return identity


def _write_update_operation_owner_info(lock_file: BinaryIO, *, owner: str) -> None:
    """寫入目前 lock 持有者資訊。"""

    payload = _UpdateOperationOwnerInfo(
        pid=os.getpid(),
        owner=owner,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(json.dumps(asdict(payload), ensure_ascii=False, indent=2).encode("utf-8"))
    lock_file.flush()


def _lock_file(lock_file: BinaryIO, lock_path: Path) -> None:
    """取得跨 process lock；失敗時回傳使用者可理解的 busy error。"""

    lock_file.seek(0)
    try:
        if os.name == "nt":
            _windows_locking(lock_file.fileno(), _windows_lock_nonblocking_flag())
        else:
            _posix_flock(lock_file.fileno(), _posix_lock_exclusive_nonblocking_flags())
    except OSError as exc:
        raise UpdateOperationLockError(
            UPDATE_OPERATION_LOCK_BUSY_MESSAGE,
            lock_path=lock_path,
        ) from exc


def _unlock_file(lock_file: BinaryIO) -> None:
    """釋放跨 process lock。"""

    lock_file.seek(0)
    if os.name == "nt":
        _windows_locking(lock_file.fileno(), _windows_unlock_flag())
    else:
        _posix_flock(lock_file.fileno(), _posix_unlock_flags())


def _windows_locking(file_descriptor: int, mode: int) -> None:
    """呼叫 Windows msvcrt locking；POSIX 分支不應進入此函式。"""

    if _msvcrt is None:
        raise RuntimeError("msvcrt is not available on this platform")
    locking = getattr(_msvcrt, "locking")
    locking(file_descriptor, mode, _LOCK_BYTE_COUNT)


def _windows_lock_nonblocking_flag() -> int:
    """回傳 Windows non-blocking lock flag。"""

    if _msvcrt is None:
        raise RuntimeError("msvcrt is not available on this platform")
    return int(getattr(_msvcrt, "LK_NBLCK"))


def _windows_unlock_flag() -> int:
    """回傳 Windows unlock flag。"""

    if _msvcrt is None:
        raise RuntimeError("msvcrt is not available on this platform")
    return int(getattr(_msvcrt, "LK_UNLCK"))


def _posix_flock(file_descriptor: int, flags: int) -> None:
    """呼叫 POSIX flock；Windows 分支不應進入此函式。"""

    if _fcntl is None:
        raise RuntimeError("fcntl is not available on this platform")
    flock = getattr(_fcntl, "flock")
    flock(file_descriptor, flags)


def _posix_lock_exclusive_nonblocking_flags() -> int:
    """回傳 POSIX exclusive non-blocking lock flags。"""

    if _fcntl is None:
        raise RuntimeError("fcntl is not available on this platform")
    return int(getattr(_fcntl, "LOCK_EX")) | int(getattr(_fcntl, "LOCK_NB"))


def _posix_unlock_flags() -> int:
    """回傳 POSIX unlock flags。"""

    if _fcntl is None:
        raise RuntimeError("fcntl is not available on this platform")
    return int(getattr(_fcntl, "LOCK_UN"))
