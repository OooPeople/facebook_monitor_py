"""Resident main worker tests。"""

from __future__ import annotations

from pathlib import Path


from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.worker.resident_runtime_errors import _is_playwright_driver_shutdown_exception
from facebook_monitor.worker.resident_shared import list_active_resident_target_ids


def test_list_active_resident_target_ids_excludes_error_runtime(tmp_path: Path) -> None:
    """resident page pool 不應保留已進入 error 的 active target page。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        active = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="active",
                canonical_url="https://www.facebook.com/groups/active",
            )
        )
        errored = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="errored",
                canonical_url="https://www.facebook.com/groups/errored",
            )
        )
        app.services.targets.restart_target_monitoring(active.id)
        app.services.targets.restart_target_monitoring(errored.id)
        app.services.targets.mark_target_error(errored.id, "terminal error")

    assert list_active_resident_target_ids(db_path) == {active.id}


def test_playwright_driver_shutdown_exception_is_classified() -> None:
    """只把 Playwright driver 關閉期間的已知背景 future 例外視為可消化噪音。"""

    assert _is_playwright_driver_shutdown_exception(
        Exception("Connection closed while reading from the driver")
    )
    assert not _is_playwright_driver_shutdown_exception(Exception("other error"))
