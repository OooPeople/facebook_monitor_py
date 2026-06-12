"""Support bundle scan and latest item collectors。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from facebook_monitor.diagnostics._support_bundle_constants import LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET
from facebook_monitor.diagnostics._support_bundle_constants import RECENT_SCAN_LIMIT_PER_TARGET
from facebook_monitor.diagnostics._support_bundle_db_common import _row_freeform_summary
from facebook_monitor.diagnostics._support_bundle_db_common import _row_int
from facebook_monitor.diagnostics._support_bundle_db_common import _row_text
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _failure_reason_from_error
from facebook_monitor.diagnostics._support_bundle_redaction import _merge_debug_metadata_counts
from facebook_monitor.diagnostics._support_bundle_redaction import _metadata_text
from facebook_monitor.diagnostics._support_bundle_redaction import _reason_count_bucket
from facebook_monitor.diagnostics._support_bundle_redaction import _safe_metadata_key_label
from facebook_monitor.diagnostics._support_bundle_redaction import _sanitize_metadata
from facebook_monitor.diagnostics._support_bundle_utils import _json_dict
from facebook_monitor.diagnostics._support_bundle_utils import _readonly_connection
from facebook_monitor.diagnostics._support_bundle_utils import _table_names


@dataclass(frozen=True)
class _ScanRunBundleRow:
    """scan_runs row 與衍生彙總 key，避免 payload builder 重複解析 metadata。"""

    row: sqlite3.Row
    metadata: dict[str, object]
    status: str
    stop_reason: str
    failure_reason: str


def _scan_summaries_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立最近 scan runs 的 redacted 摘要。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing", "runs": []}
    with _readonly_connection(db_path) as connection:
        if "scan_runs" not in _table_names(connection):
            return {"available": False, "reason": "scan_runs_table_missing", "runs": []}
        rows = connection.execute(
            """
            SELECT *
            FROM (
                SELECT scan_runs.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY id DESC
                       ) AS row_number
                FROM scan_runs
            )
            WHERE row_number <= ?
            ORDER BY target_id, id DESC
            """,
            (RECENT_SCAN_LIMIT_PER_TARGET,),
        ).fetchall()
    scan_rows = [_scan_run_bundle_row(row) for row in rows]
    status_counts, stop_reason_counts, failure_reason_counts = _scan_run_counts(scan_rows)
    return {
        "available": True,
        "limit_per_target": RECENT_SCAN_LIMIT_PER_TARGET,
        "status_counts": dict(sorted(status_counts.items())),
        "stop_reason_counts": dict(sorted(stop_reason_counts.items())),
        "failure_reason_counts": dict(sorted(failure_reason_counts.items())),
        "runs": [
            _scan_run_payload(scan_row, aliases)
            for scan_row in scan_rows
        ],
    }


def _scan_run_bundle_row(row: sqlite3.Row) -> _ScanRunBundleRow:
    """整理單筆 scan_runs row 與後續 count 需要的 reason bucket。"""

    metadata = _json_dict(row["metadata"])
    return _ScanRunBundleRow(
        row=row,
        metadata=metadata,
        status=_row_text(row, "status"),
        stop_reason=_reason_count_bucket(
            _metadata_text(metadata, "stop_reason")
            or _metadata_text(metadata, "skip_reason")
        ),
        failure_reason=_reason_count_bucket(
            _metadata_text(metadata, "reason")
            or _failure_reason_from_error(_row_text(row, "error_message"))
        ),
    )


def _scan_run_counts(
    scan_rows: list[_ScanRunBundleRow],
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    """彙總最近 scan runs 的狀態、停止原因與失敗原因。"""

    status_counts: Counter[str] = Counter()
    stop_reason_counts: Counter[str] = Counter()
    failure_reason_counts: Counter[str] = Counter()
    for scan_row in scan_rows:
        status_counts[scan_row.status] += 1
        if scan_row.stop_reason:
            stop_reason_counts[scan_row.stop_reason] += 1
        if scan_row.failure_reason:
            failure_reason_counts[scan_row.failure_reason] += 1
    return status_counts, stop_reason_counts, failure_reason_counts


def _scan_run_payload(
    scan_row: _ScanRunBundleRow,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """整理單筆 scan_runs support bundle payload。"""

    row = scan_row.row
    return {
        "scan_run": aliases.alias("scan_run", row["id"]),
        "target": aliases.alias("target", row["target_id"]),
        "started_at": _row_text(row, "started_at"),
        "finished_at": _row_text(row, "finished_at"),
        "status": scan_row.status,
        "item_count": _row_int(row, "item_count"),
        "matched_count": _row_int(row, "matched_count"),
        "worker_mode": _row_text(row, "worker_mode"),
        "error_message": _row_freeform_summary(row, "error_message", aliases),
        "metadata": _sanitize_metadata(scan_row.metadata),
    }


def _latest_scan_debug_summary_payload(
    db_path: Path,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """彙整 latest scan items 的 debug metadata，不輸出內容文字。"""

    if not db_path.is_file():
        return {"available": False, "reason": "database_missing", "targets": []}
    with _readonly_connection(db_path) as connection:
        if "latest_scan_items" not in _table_names(connection):
            return {
                "available": False,
                "reason": "latest_scan_items_table_missing",
                "targets": [],
            }
        rows = connection.execute(
            """
            SELECT *
            FROM (
                SELECT latest_scan_items.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_id
                           ORDER BY item_index
                       ) AS row_number
                FROM latest_scan_items
            )
            WHERE row_number <= ?
            ORDER BY target_id, item_index
            """,
            (LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET,),
        ).fetchall()
        aggregate_rows = connection.execute(
            """
            SELECT
                target_id,
                COUNT(*) AS item_count,
                SUM(CASE WHEN TRIM(text) = '' THEN 1 ELSE 0 END) AS empty_text_count,
                SUM(CASE WHEN TRIM(matched_keyword) != '' THEN 1 ELSE 0 END)
                    AS matched_item_count
            FROM latest_scan_items
            GROUP BY target_id
            ORDER BY target_id
            """
        ).fetchall()
    targets = _latest_scan_targets_from_aggregates(aggregate_rows, aliases)
    for row in rows:
        _append_latest_scan_sample(targets, row, aliases)
    return {
        "available": True,
        "sample_limit_per_target": LATEST_ITEM_SAMPLE_LIMIT_PER_TARGET,
        "targets": list(targets.values()),
    }


def _latest_scan_targets_from_aggregates(
    rows: list[sqlite3.Row],
    aliases: _SupportBundleAliases,
) -> dict[str, dict[str, object]]:
    """依 target 建立 latest scan debug aggregate payload。"""

    return {
        _row_text(row, "target_id"): _latest_scan_target_payload(row, aliases)
        for row in rows
    }


def _latest_scan_target_payload(
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """整理單一 target 的 latest_scan_items aggregate row。"""

    return {
        "target": aliases.alias("target", row["target_id"]),
        "item_count": _row_int(row, "item_count"),
        "empty_text_count": _row_int(row, "empty_text_count"),
        "matched_item_count": _row_int(row, "matched_item_count"),
        "debug_key_counts": {},
        "debug_value_counts": {},
        "samples": [],
    }


def _empty_latest_scan_target_payload(
    target_id: str,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """建立沒有 aggregate row 時的 latest scan target payload。"""

    return {
        "target": aliases.alias("target", target_id),
        "item_count": 0,
        "empty_text_count": 0,
        "matched_item_count": 0,
        "debug_key_counts": {},
        "debug_value_counts": {},
        "samples": [],
    }


def _append_latest_scan_sample(
    targets: dict[str, dict[str, object]],
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
) -> None:
    """把單筆 latest_scan_items sample 併入對應 target payload。"""

    target_id = _row_text(row, "target_id")
    target_payload = targets.setdefault(
        target_id,
        _empty_latest_scan_target_payload(target_id, aliases),
    )
    metadata = _json_dict(row["debug_metadata"])
    _merge_debug_metadata_counts(target_payload, metadata)
    samples = target_payload["samples"]
    if isinstance(samples, list):
        samples.append(_latest_scan_sample_payload(row, aliases, metadata))


def _latest_scan_sample_payload(
    row: sqlite3.Row,
    aliases: _SupportBundleAliases,
    metadata: dict[str, object],
) -> dict[str, object]:
    """整理單筆 latest_scan_items sample，不輸出 raw text/id/url。"""

    return {
        "item": aliases.alias("item", row["item_key"]),
        "scan_run": aliases.alias("scan_run", row["scan_run_id"]),
        "kind": _row_text(row, "item_kind"),
        "item_index": _row_int(row, "item_index"),
        "text_length": len(_row_text(row, "text")),
        "has_author": bool(_row_text(row, "author")),
        "has_permalink": bool(_row_text(row, "permalink")),
        "matched": bool(_row_text(row, "matched_keyword").strip()),
        "debug_keys": sorted(
            _safe_metadata_key_label(key) for key in metadata.keys()
        )[:20],
        "scanned_at": _row_text(row, "scanned_at"),
    }



__all__ = [
    "_latest_scan_debug_summary_payload",
    "_scan_summaries_payload",
]
