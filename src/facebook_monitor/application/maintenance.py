"""本機資料維護服務。

職責：提供 Web UI 與 resident worker 共用的低風險 housekeeping 入口，
避免 bounded retention 只依賴 scheduler tick。
"""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import sqlite3

from facebook_monitor.core.defaults import PYTHON_PERSISTENCE_RETENTION_DEFAULTS
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.maintenance import RuntimeDataMaintenanceRepository
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error


logger = logging.getLogger(__name__)
_LAST_RETENTION_MAINTENANCE_BY_DB: dict[Path, datetime] = {}
_LAST_RETENTION_ATTEMPT_BY_DB: dict[Path, datetime] = {}


def run_bounded_retention_maintenance_for_db(
    db_path: Path,
    *,
    now: datetime | None = None,
    interval_seconds: int = (
        PYTHON_PERSISTENCE_RETENTION_DEFAULTS.maintenance_interval_seconds
    ),
    retry_interval_seconds: int = (
        PYTHON_PERSISTENCE_RETENTION_DEFAULTS.maintenance_retry_interval_seconds
    ),
) -> int:
    """節流執行 bounded retention；失敗不影響呼叫端主流程。"""

    resolved_db_path = Path(db_path).resolve()
    current_time = now or utc_now()
    last_attempt = _LAST_RETENTION_ATTEMPT_BY_DB.get(resolved_db_path)
    if (
        last_attempt is not None
        and (current_time - last_attempt).total_seconds() < max(retry_interval_seconds, 0)
    ):
        return 0
    last_run = _LAST_RETENTION_MAINTENANCE_BY_DB.get(resolved_db_path)
    if (
        last_run is not None
        and (current_time - last_run).total_seconds() < max(interval_seconds, 0)
    ):
        return 0
    _LAST_RETENTION_ATTEMPT_BY_DB[resolved_db_path] = current_time
    try:
        connection = sqlite3.connect(
            f"{resolved_db_path.as_uri()}?mode=rw",
            uri=True,
            timeout=0.1,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 100")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            result = RuntimeDataMaintenanceRepository(connection).prune_bounded_retention(
                now=current_time
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        _LAST_RETENTION_MAINTENANCE_BY_DB[resolved_db_path] = current_time
        return result.total_deleted
    except sqlite3.OperationalError as exc:
        if is_sqlite_lock_error(exc):
            logger.warning("bounded retention maintenance skipped: database locked")
            return 0
        logger.exception("bounded retention maintenance failed")
        return 0
    except Exception:
        logger.exception("bounded retention maintenance failed")
        return 0
