"""Dashboard revision read-only query."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.webapp.dashboard_read_models import DashboardRevision
from facebook_monitor.webapp.dashboard_read_models import DashboardRevisionUnavailable


def get_dashboard_revision(db_path: Path) -> DashboardRevision:
    """用 read-only connection 讀取首頁 revision，避免 SSE 觸發 schema init。"""

    if not db_path.exists():
        return DashboardRevision(revision="0", last_changed_at="")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1)
    except sqlite3.OperationalError as exc:
        if is_sqlite_lock_error(exc):
            raise DashboardRevisionUnavailable(str(exc)) from exc
        return DashboardRevision(revision="0", last_changed_at="")
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 1000")
        row = connection.execute(
            "SELECT revision, updated_at FROM dashboard_revision WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if is_sqlite_lock_error(exc):
            raise DashboardRevisionUnavailable(str(exc)) from exc
        if "no such table" not in message:
            raise
        return DashboardRevision(revision="0", last_changed_at="")
    finally:
        connection.close()
    if row is None:
        return DashboardRevision(revision="0", last_changed_at="")
    return DashboardRevision(
        revision=str(row["revision"]),
        last_changed_at=row["updated_at"],
    )
