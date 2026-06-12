"""Cover image URL host diagnostics。

職責：以隱私安全的方式統計 Facebook cover image URL host，供日後縮窄
allowlist 前評估實際合法來源。輸出不得包含完整 URL、path、query 或 target id。
"""

from __future__ import annotations

from collections import Counter
import sqlite3
from urllib.parse import urlsplit

from facebook_monitor.core.external_url_policy import FACEBOOK_IMAGE_ALLOWED_HOST_SUFFIXES
from facebook_monitor.core.external_url_policy import sanitize_facebook_image_url


COVER_IMAGE_HOST_SOURCE_FIELDS = (
    ("targets", "group_cover_image_url"),
    ("target_cover_image_refresh_state", "last_reported_url"),
    ("target_cover_image_refresh_state", "last_resolved_url"),
)


def collect_cover_image_host_report(connection: sqlite3.Connection) -> dict[str, object]:
    """回傳不含完整 URL 的 cover image host 統計。"""

    table_names = _table_names(connection)
    overall = _HostStats()
    by_field: dict[str, dict[str, object]] = {}
    for table_name, column_name in COVER_IMAGE_HOST_SOURCE_FIELDS:
        source_key = f"{table_name}.{column_name}"
        if table_name not in table_names:
            by_field[source_key] = _unavailable_field_payload("table_missing")
            continue
        if column_name not in _table_columns(connection, table_name):
            by_field[source_key] = _unavailable_field_payload("column_missing")
            continue
        field_stats = _HostStats()
        rows = connection.execute(f"SELECT {column_name} FROM {table_name}").fetchall()
        for row in rows:
            _record_cover_image_value(field_stats, _row_first_value(row))
        overall.merge(field_stats)
        by_field[source_key] = field_stats.to_payload(available=True)
    return {
        "available": True,
        "source_fields": [
            f"{table_name}.{column_name}"
            for table_name, column_name in COVER_IMAGE_HOST_SOURCE_FIELDS
        ],
        "overall": overall.to_payload(available=True),
        "by_field": by_field,
    }


class _HostStats:
    """累積 cover image host 統計。"""

    def __init__(self) -> None:
        self.value_count = 0
        self.empty_count = 0
        self.accepted_count = 0
        self.rejected_count = 0
        self.accepted_host_counts: Counter[str] = Counter()
        self.accepted_suffix_counts: Counter[str] = Counter()
        self.reject_reason_counts: Counter[str] = Counter()

    def merge(self, other: _HostStats) -> None:
        """合併另一組統計。"""

        self.value_count += other.value_count
        self.empty_count += other.empty_count
        self.accepted_count += other.accepted_count
        self.rejected_count += other.rejected_count
        self.accepted_host_counts.update(other.accepted_host_counts)
        self.accepted_suffix_counts.update(other.accepted_suffix_counts)
        self.reject_reason_counts.update(other.reject_reason_counts)

    def to_payload(self, *, available: bool) -> dict[str, object]:
        """轉成可寫入 support bundle 的 JSON payload。"""

        return {
            "available": available,
            "value_count": self.value_count,
            "empty_count": self.empty_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "accepted_host_counts": dict(sorted(self.accepted_host_counts.items())),
            "accepted_suffix_counts": dict(sorted(self.accepted_suffix_counts.items())),
            "reject_reason_counts": dict(sorted(self.reject_reason_counts.items())),
        }


def _record_cover_image_value(stats: _HostStats, value: object) -> None:
    """統計單一 cover image URL 值，不保留 raw URL。"""

    stats.value_count += 1
    raw = str(value or "").strip()
    if not raw:
        stats.empty_count += 1
        return
    result = sanitize_facebook_image_url(raw)
    if not result.ok:
        stats.rejected_count += 1
        stats.reject_reason_counts[result.reason or "unknown"] += 1
        return
    host = (urlsplit(result.url).hostname or "").casefold().rstrip(".")
    if not host:
        stats.rejected_count += 1
        stats.reject_reason_counts["sanitized_host_missing"] += 1
        return
    stats.accepted_count += 1
    stats.accepted_host_counts[host] += 1
    stats.accepted_suffix_counts[_allowed_suffix_for_host(host)] += 1


def _allowed_suffix_for_host(host: str) -> str:
    """回傳 host 命中的既有 allowlist suffix。"""

    for suffix in FACEBOOK_IMAGE_ALLOWED_HOST_SUFFIXES:
        normalized_suffix = suffix.casefold().lstrip(".")
        if host == normalized_suffix or host.endswith("." + normalized_suffix):
            return normalized_suffix
    return "unknown_allowed_suffix"


def _unavailable_field_payload(reason: str) -> dict[str, object]:
    """回傳缺 table / column 時的欄位 payload。"""

    return {
        "available": False,
        "reason": reason,
        "value_count": 0,
        "empty_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "accepted_host_counts": {},
        "accepted_suffix_counts": {},
        "reject_reason_counts": {},
    }


def _table_names(connection: sqlite3.Connection) -> set[str]:
    """讀取目前 DB table 名稱。"""

    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    """讀取指定 table 欄位名稱。"""

    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _row_first_value(row: object) -> object:
    """讀取 sqlite Row / tuple 的第一個欄位值。"""

    try:
        return row[0]  # type: ignore[index]
    except (IndexError, TypeError):
        return ""
