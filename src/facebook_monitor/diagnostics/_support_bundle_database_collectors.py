"""Support bundle database health collectors。"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
import sqlite3

from facebook_monitor.diagnostics._support_bundle_db_common import _SUPPORT_COUNT_TABLES
from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _safe_exception_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _support_row_id_hash
from facebook_monitor.diagnostics._support_bundle_utils import _isoformat_or_empty
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_columns
from facebook_monitor.diagnostics._support_bundle_utils import _table_count
from facebook_monitor.diagnostics._support_bundle_utils import _table_names
from facebook_monitor.persistence.invariants import DatabaseInvariantViolation
from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.schema import MIN_SUPPORTED_SCHEMA_VERSION
from facebook_monitor.persistence.schema import SCHEMA_VERSION
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.sqlite_codec import read_schema_version
from facebook_monitor.runtime.paths import RuntimePaths
from facebook_monitor.updates.validation import is_reparse_or_symlink


def _database_summary_payload(db_path: Path) -> dict[str, object]:
    """建立只含 counts 與 invariant 結果的 DB 摘要。"""

    if not db_path.is_file():
        return {
            "available": False,
            "reason": "database_missing",
            "table_counts": {table_name: 0 for table_name in _SUPPORT_COUNT_TABLES},
            "notification_outbox": _empty_outbox_summary(),
            "invariant_violation_count": 0,
            "invariant_violations": [],
        }
    with _readonly_connection(db_path) as connection:
        connection.row_factory = sqlite3.Row
        table_names = _table_names(connection)
        table_counts = {
            table_name: _table_count(connection, table_name) if table_name in table_names else 0
            for table_name in _SUPPORT_COUNT_TABLES
        }
        outbox_summary = (
            NotificationOutboxRepository(
                connection,
                secret_codec=PlaintextSecretCodec(),
            ).summarize_all()
            if "notification_outbox" in table_names
            else None
        )
        violations: tuple[DatabaseInvariantViolation, ...]
        try:
            violations = validate_database_invariants(connection)
            invariant_error = ""
        except Exception as exc:
            violations = ()
            invariant_error = _safe_exception_summary(exc)
    return {
        "available": True,
        "table_counts": table_counts,
        "notification_outbox": (
            {
                "pending": outbox_summary.pending_count,
                "processing": outbox_summary.processing_count,
                "failed": outbox_summary.failed_count,
                "terminal": outbox_summary.terminal_count,
                "oldest_pending_updated_at": _isoformat_or_empty(
                    outbox_summary.oldest_pending_updated_at
                ),
                "max_attempts": outbox_summary.max_attempts,
            }
            if outbox_summary is not None
            else _empty_outbox_summary()
        ),
        "invariant_violation_count": len(violations),
        "invariant_error": invariant_error,
        "invariant_violations": [
            {
                "table": violation.table,
                "row_id_hash": _support_row_id_hash(
                    table=violation.table,
                    row_id=violation.row_id,
                ),
                "field": violation.field,
                "message": _freeform_summary(violation.message),
            }
            for violation in violations[:100]
        ],
    }


def _database_health_payload(paths: RuntimePaths) -> dict[str, object]:
    """建立 SQLite schema 與檔案健康摘要。"""

    payload: dict[str, object] = {
        "available": paths.db_path.is_file(),
        "schema_version": 0,
        "supported_schema_version": SCHEMA_VERSION,
        "min_supported_schema_version": MIN_SUPPORTED_SCHEMA_VERSION,
        "files": _database_file_summaries(paths.db_path),
        "quick_check": "not_run",
        "tables": {},
        "dashboard_revision": None,
    }
    if not paths.db_path.is_file():
        return payload | {"reason": "database_missing"}
    with _readonly_connection(paths.db_path) as connection:
        table_names = _table_names(connection)
        payload["schema_version"] = read_schema_version(connection)
        payload["tables"] = {
            table_name: {
                "exists": table_name in table_names,
                "columns": _table_columns(connection, table_name)
                if table_name in table_names
                else [],
            }
            for table_name in _SUPPORT_COUNT_TABLES
        }
        try:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
            payload["quick_check"] = str(row[0] if row else "")
        except sqlite3.DatabaseError as exc:
            payload["quick_check"] = _safe_exception_summary(exc)
        if "dashboard_revision" in table_names:
            row = connection.execute(
                "SELECT revision, updated_at FROM dashboard_revision WHERE id = 1"
            ).fetchone()
            if row:
                payload["dashboard_revision"] = {
                    "revision": str(row["revision"]),
                    "updated_at": str(row["updated_at"] or ""),
                }
    return payload

def _database_file_summaries(db_path: Path) -> list[dict[str, object]]:
    """回傳 DB / WAL / SHM 檔案大小與 mtime 摘要。"""

    paths = (
        ("app.db", db_path),
        ("app.db-wal", db_path.with_name(f"{db_path.name}-wal")),
        ("app.db-shm", db_path.with_name(f"{db_path.name}-shm")),
    )
    summaries = []
    for label, path in paths:
        summary: dict[str, object] = {"file": label, "exists": False}
        try:
            if path.is_file() and not is_reparse_or_symlink(path):
                stat = path.stat()
                summary.update(
                    {
                        "exists": True,
                        "size_bytes": stat.st_size,
                        "mtime": datetime.fromtimestamp(
                            stat.st_mtime,
                            tz=timezone.utc,
                        ).isoformat(),
                    }
                )
        except OSError as exc:
            summary["error"] = _safe_exception_summary(exc)
        summaries.append(summary)
    return summaries

def _empty_outbox_summary() -> dict[str, object]:
    """回傳空 DB 時的 notification outbox 摘要。"""

    return {
        "pending": 0,
        "processing": 0,
        "failed": 0,
        "terminal": 0,
        "oldest_pending_updated_at": "",
        "max_attempts": 0,
    }


__all__ = [
    "_database_health_payload",
    "_database_summary_payload",
]
