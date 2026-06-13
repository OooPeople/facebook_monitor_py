"""Resident scan DB connection policy。"""

from __future__ import annotations

from facebook_monitor.application.context import ApplicationContext


RESIDENT_SCAN_DB_BUSY_TIMEOUT_MS = 100


def set_resident_scan_db_busy_timeout(
    app: ApplicationContext,
    timeout_ms: int,
) -> None:
    """設定 resident scan event-loop DB connection 的 lock 等待上限。"""

    bounded_timeout = max(int(timeout_ms), 0)
    app.repositories.runtime_states.connection.execute(f"PRAGMA busy_timeout = {bounded_timeout}")
