"""Automation profile lease。

職責：用單一模組管理 Playwright persistent profile 的使用權，
避免同一個 automation profile 同時被多個 context 開啟。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from threading import Lock
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


LOCK_FILE_NAME = ".facebook_monitor_profile.lock"
LOCK_BYTE_COUNT = 1

_ACTIVE_PROFILE_PATHS: set[Path] = set()
_ACTIVE_PROFILE_PATHS_LOCK = Lock()


class ProfileLeaseError(RuntimeError):
    """表示 automation profile 已被其他工作持有。"""


@dataclass(frozen=True)
class ProfileLease:
    """保存目前持有的 automation profile lease 資訊。"""

    profile_dir: Path
    lock_path: Path
    owner: str


@contextmanager
def acquire_profile_lease(profile_dir: Path, owner: str) -> Iterator[ProfileLease]:
    """取得 automation profile 使用權，離開 context 時自動釋放。"""

    resolved_profile_dir = profile_dir.resolve()
    lock_path = resolved_profile_dir / LOCK_FILE_NAME
    _claim_in_process(resolved_profile_dir, owner)
    lock_file: BinaryIO | None = None
    locked = False
    try:
        resolved_profile_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+b")
        _lock_file(lock_file, lock_path, owner)
        locked = True
        _write_owner(lock_file, owner)
        yield ProfileLease(
            profile_dir=resolved_profile_dir,
            lock_path=lock_path,
            owner=owner,
        )
    finally:
        try:
            if lock_file is not None:
                if locked:
                    _unlock_file(lock_file)
                lock_file.close()
        finally:
            _release_in_process(resolved_profile_dir)


def _claim_in_process(profile_dir: Path, owner: str) -> None:
    """先擋下同一 Python process 內的重複 profile 使用。"""

    with _ACTIVE_PROFILE_PATHS_LOCK:
        if profile_dir in _ACTIVE_PROFILE_PATHS:
            raise ProfileLeaseError(
                f"automation profile 目前已在本程式內使用中: {profile_dir} (request={owner})"
            )
        _ACTIVE_PROFILE_PATHS.add(profile_dir)


def _release_in_process(profile_dir: Path) -> None:
    """釋放同一 Python process 內的 profile 使用標記。"""

    with _ACTIVE_PROFILE_PATHS_LOCK:
        _ACTIVE_PROFILE_PATHS.discard(profile_dir)


def _lock_file(lock_file: BinaryIO, lock_path: Path, owner: str) -> None:
    """取得跨 process lock；失敗時回報目前鎖檔資訊。"""

    lock_file.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, LOCK_BYTE_COUNT)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        existing_owner = _read_owner(lock_path)
        owner_hint = f"；目前持有者: {existing_owner}" if existing_owner else ""
        raise ProfileLeaseError(
            f"automation profile 目前被其他工作使用中: {lock_path.parent}"
            f" (request={owner}){owner_hint}"
        ) from exc


def _unlock_file(lock_file: BinaryIO) -> None:
    """釋放跨 process lock。"""

    lock_file.seek(0)
    if os.name == "nt":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, LOCK_BYTE_COUNT)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_owner(lock_file: BinaryIO, owner: str) -> None:
    """寫入目前 lease 持有者，方便除錯 profile busy 問題。"""

    lock_file.seek(0)
    lock_file.truncate()
    payload = f"owner={owner}\npid={os.getpid()}\n".encode("utf-8")
    lock_file.write(payload)
    lock_file.flush()


def _read_owner(lock_path: Path) -> str:
    """讀取鎖檔內的持有者資訊；讀不到時回傳空字串。"""

    try:
        return lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
