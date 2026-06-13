"""Support bundle DB collector shared helpers。"""

from __future__ import annotations

import sqlite3

from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_utils import _json_list
from facebook_monitor.persistence.schema import CURRENT_SCHEMA_TABLES


def _row_text(row: sqlite3.Row, column: str) -> str:
    """從 SQLite row 取出空值安全字串。"""

    return str(row[column] or "")


def _row_int(row: sqlite3.Row, column: str) -> int:
    """從 SQLite row 取出空值安全整數。"""

    return int(row[column] or 0)


def _row_freeform_summary(
    row: sqlite3.Row,
    column: str,
    aliases: _SupportBundleAliases,
) -> dict[str, object]:
    """從 SQLite row 取出 freeform 欄位並套用 support bundle 摘要。"""

    return _freeform_summary(_row_text(row, column), aliases=aliases)

def _include_keyword_group_summary(value: str) -> dict[str, int]:
    """計算 include keyword groups 數量，不輸出 keyword。"""

    groups = _json_list(value)
    group_count = 0
    nonempty_group_count = 0
    keyword_count = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_count += 1
        keywords = group.get("keywords", [])
        if isinstance(keywords, list):
            normalized_keywords = [item for item in keywords if str(item).strip()]
            keyword_count += len(normalized_keywords)
            if normalized_keywords:
                nonempty_group_count += 1
    return {
        "group_count": group_count,
        "nonempty_group_count": nonempty_group_count,
        "keyword_count": keyword_count,
    }


_SUPPORT_COUNT_TABLES = CURRENT_SCHEMA_TABLES


__all__ = [
    "_SUPPORT_COUNT_TABLES",
    "_include_keyword_group_summary",
    "_row_freeform_summary",
    "_row_int",
    "_row_text",
]
