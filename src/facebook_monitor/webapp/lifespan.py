"""Web UI lifespan startup/shutdown sequence。

職責：集中 FastAPI lifespan 內的啟動維護、runtime reset、target reset 與 scheduler
生命週期，保持 `create_app()` 只負責組裝。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.maintenance import run_bounded_retention_maintenance_for_db
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions


@asynccontextmanager
async def webui_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """管理 Web UI 啟動與關閉時的背景 scheduler 生命週期。"""

    _run_startup_maintenance(app)
    await _start_dashboard_revision_notifier(app)
    try:
        _start_scheduler_if_configured(app)
        yield
    finally:
        await _shutdown_webui_runtime(app)


def _run_startup_maintenance(app: FastAPI) -> None:
    """依既有順序執行 Web UI startup maintenance。"""

    with SqliteApplicationContext(app.state.db_path) as app_context:
        app_context.repositories.match_history.prune_all_target_limits()
    run_bounded_retention_maintenance_for_db(app.state.db_path)
    if app.state.reset_runtime_data_on_startup:
        with SqliteApplicationContext(app.state.db_path) as app_context:
            app_context.repositories.maintenance.clear_startup_runtime_data()
    if app.state.reset_targets_on_startup:
        with SqliteApplicationContext(app.state.db_path) as app_context:
            app_context.services.targets.pause_all_targets_for_webui_startup(
                default_fixed_refresh_sec=app.state.scheduler_interval_seconds,
            )


def _start_scheduler_if_configured(app: FastAPI) -> None:
    """依 app state 啟動 background scheduler。"""

    if not app.state.auto_start_scheduler:
        return
    app.state.scheduler_manager.start(
        SchedulerSessionOptions(
            db_path=app.state.db_path,
            profile_dir=app.state.profile_dir,
            interval_seconds=app.state.scheduler_interval_seconds,
            scheduler_tick_seconds=app.state.scheduler_tick_seconds,
            max_concurrent_scans=app.state.max_concurrent_scans,
        )
    )


async def _start_dashboard_revision_notifier(app: FastAPI) -> None:
    """在 startup maintenance 後啟動 dashboard revision watcher。"""

    await app.state.dashboard_revision_notifier.start()


async def _shutdown_webui_runtime(app: FastAPI) -> None:
    """關閉 Web UI 背景資源，保留既有 nested-finally 收尾語義。"""

    try:
        await app.state.dashboard_revision_notifier.stop()
    finally:
        try:
            await app.state.bounded_retention_maintenance_runner.wait_until_idle()
        finally:
            try:
                app.state.profile_manager.close()
            finally:
                app.state.scheduler_manager.stop()


__all__ = ["webui_lifespan"]
