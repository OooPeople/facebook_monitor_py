"""Updater operation lock tests。"""

from __future__ import annotations

import json
import os

import pytest

from facebook_monitor.runtime.update_operation_lock import acquire_update_operation_lock
from facebook_monitor.runtime.update_operation_lock import UPDATE_OPERATION_LOCK_FILE_NAME
from facebook_monitor.runtime.update_operation_lock import UpdateOperationLockError


def test_update_operation_lock_rejects_same_process_reentry(tmp_path) -> None:
    """同一 process 重複進入更新操作必須被擋下。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_update_operation_lock(runtime_dir, "first") as lock:
        assert lock.lock_path.name == UPDATE_OPERATION_LOCK_FILE_NAME
        assert lock.lock_path.is_file()
        with pytest.raises(UpdateOperationLockError):
            with acquire_update_operation_lock(runtime_dir, "second"):
                pass


def test_update_operation_lock_releases_and_overwrites_owner_info(tmp_path) -> None:
    """釋放後可再次取得 lock，lock 檔只保留最新持有者資訊。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_update_operation_lock(runtime_dir, "first"):
        pass
    with acquire_update_operation_lock(runtime_dir, "second") as lock:
        lock_path = lock.lock_path
    payload = json.loads(lock_path.read_text(encoding="utf-8"))

    assert payload["owner"] == "second"
    assert payload["pid"] == os.getpid()
    assert payload["started_at"]
