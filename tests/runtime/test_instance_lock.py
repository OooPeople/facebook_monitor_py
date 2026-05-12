"""Application instance lock tests。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from facebook_monitor.runtime import instance_lock
from facebook_monitor.runtime.instance_lock import acquire_app_instance_lock
from facebook_monitor.runtime.instance_lock import acquire_resource_identity_lock
from facebook_monitor.runtime.instance_lock import _canonical_resource_identity_path
from facebook_monitor.runtime.instance_lock import read_server_info


def test_instance_lock_writes_and_clears_server_info(tmp_path) -> None:
    """持有 app lock 時可寫入 server.json，結束時可清除。"""

    runtime_dir = tmp_path / "runtime"

    with acquire_app_instance_lock(runtime_dir, "test") as instance_lock:
        info = instance_lock.write_server_info(
            host="127.0.0.1",
            port=8765,
            url="http://127.0.0.1:8765",
        )

        loaded = read_server_info(runtime_dir)

        assert loaded == info
        assert loaded is not None
        assert loaded.url == "http://127.0.0.1:8765"
        instance_lock.clear_server_info()
        assert read_server_info(runtime_dir) is None


def test_read_server_info_ignores_invalid_json(tmp_path) -> None:
    """server.json 壞掉時不讓 launcher 崩潰。"""

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "server.json").write_text("{not-json", encoding="utf-8")

    assert read_server_info(runtime_dir) is None


def test_resource_identity_path_uses_normcase_on_windows(monkeypatch) -> None:
    """Windows resource lock identity 應避免大小寫差異繞過互斥。"""

    monkeypatch.setattr(instance_lock.os, "name", "nt")
    monkeypatch.setattr(
        instance_lock.os.path,
        "normcase",
        lambda value: value.lower().replace("/", "\\"),
    )

    canonical = _canonical_resource_identity_path(Path("C:/Temp/Facebook/App.DB"))

    assert canonical.endswith("\\temp\\facebook\\app.db")


def test_resource_lock_writes_owner_info(tmp_path) -> None:
    """resource lock 檔保留持有者資訊，方便排查衝突。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()

    with acquire_resource_identity_lock(
        db_path=db_path,
        profile_dir=profile_dir,
        owner="test-owner",
    ) as resource_lock:
        lock_paths = resource_lock.lock_paths

    payloads = [
        json.loads(lock_path.read_text(encoding="utf-8"))
        for lock_path in lock_paths
    ]

    assert {payload["resource_kind"] for payload in payloads} == {"db", "profile"}
    assert {payload["owner"] for payload in payloads} == {"test-owner"}
    assert {payload["pid"] for payload in payloads} == {os.getpid()}
    assert all(payload["resource_path"] for payload in payloads)
    assert all(payload["started_at"] for payload in payloads)
