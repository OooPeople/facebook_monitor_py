"""SQLite encoding / decoding helpers。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from facebook_monitor.core.keyword_groups import normalize_include_keyword_groups
from facebook_monitor.core.models import IncludeKeywordGroup
from facebook_monitor.core.models import TargetRuntimeStatus


def encode_datetime(value: datetime | None) -> str:
    """將 datetime 轉成 SQLite 可保存的 ISO 字串。"""

    return value.isoformat() if value else ""


def decode_datetime(value: str) -> datetime | None:
    """將 SQLite ISO 字串還原為 datetime。"""

    return datetime.fromisoformat(value) if value else None


def encode_keywords(values: Iterable[str]) -> str:
    """將 keyword tuple/list 編碼為 JSON。"""

    return json.dumps(list(values), ensure_ascii=False)


def decode_keywords(value: str) -> tuple[str, ...]:
    """將 keyword JSON 還原為 tuple。"""

    if not value:
        return ()
    return tuple(str(item) for item in json.loads(value))


def encode_include_keyword_groups(values: Iterable[IncludeKeywordGroup]) -> str:
    """將 include keyword groups 編碼為 JSON。"""

    groups = normalize_include_keyword_groups(values, fill_empty_slots=True)
    return json.dumps(
        [
            {
                "group_id": group.group_id,
                "label": group.label,
                "keywords": list(group.keywords),
            }
            for group in groups
        ],
        ensure_ascii=False,
    )


def decode_include_keyword_groups(value: str) -> tuple[IncludeKeywordGroup, ...]:
    """將 include keyword groups JSON 還原為固定 group slots。"""

    if not value:
        return ()
    raw_groups = json.loads(value)
    if not isinstance(raw_groups, list):
        return ()
    groups: list[IncludeKeywordGroup] = []
    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            continue
        raw_keywords = raw_group.get("keywords", ())
        keywords = (
            tuple(str(item) for item in raw_keywords)
            if isinstance(raw_keywords, list)
            else ()
        )
        group_id = str(raw_group.get("group_id") or index)
        groups.append(
            IncludeKeywordGroup(
                group_id=group_id,
                label=str(raw_group.get("label") or ""),
                keywords=keywords,
            )
        )
    return normalize_include_keyword_groups(groups, fill_empty_slots=True)


def decode_runtime_status(value: str) -> TargetRuntimeStatus:
    """將舊版 runtime status 正規化為目前 executor 狀態。"""

    if value == "paused":
        return TargetRuntimeStatus.IDLE
    return TargetRuntimeStatus(value)


def read_schema_version(connection: sqlite3.Connection) -> int:
    """讀取目前 schema version；舊 DB 尚無 metadata 時回傳 0。"""

    try:
        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None:
        return 0
    try:
        raw_value = row["value"]
    except (TypeError, IndexError):
        raw_value = row[0]
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return 0


def write_schema_version(connection: sqlite3.Connection, version: int) -> None:
    """寫入 schema version；呼叫端應在 migration 成功後才呼叫。"""

    connection.execute(
        """
        INSERT OR REPLACE INTO schema_metadata (key, value)
        VALUES ('version', ?)
        """,
        (str(version),),
    )
