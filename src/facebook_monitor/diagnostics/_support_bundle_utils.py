"""Support bundle SQLite and scalar utility helpers。

職責：提供唯讀 SQLite 連線、table introspection、JSON parsing 與
小型型別轉換；本模組不做 redaction 決策。
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from datetime import timezone
import json
import sqlite3
from pathlib import Path

def _readonly_connection(db_path: Path) -> closing[sqlite3.Connection]:
    """開啟 SQLite 唯讀連線。"""

    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 500")
    return closing(connection)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    """讀取目前 DB 內所有 table 名稱。"""

    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row["name"]) for row in rows}


def _table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    """讀取 table 欄位名稱。"""

    return [
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]


def _table_count(connection: sqlite3.Connection, table_name: str) -> int:
    """讀取單一 table count。"""

    row = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
    return int(row["count"] if row else 0)


def _json_list_length(value: str) -> int:
    """安全計算 JSON list 長度。"""

    return len(_json_list(value))


def _json_list(value: str) -> list[object]:
    """讀取 JSON list，失敗時回空 list。"""

    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _json_dict(value: object) -> dict[str, object]:
    """讀取 JSON dict，失敗時回空 dict。"""

    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _age_seconds(value: object, now: datetime) -> int | None:
    """計算 timestamp 到 now 的秒數，失敗時回 None。"""

    timestamp = _parse_datetime(value)
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(int((now - timestamp.astimezone(timezone.utc)).total_seconds()), 0)


def _parse_datetime(value: object) -> datetime | None:
    """安全解析 ISO datetime。"""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _isoformat_or_empty(value: datetime | None) -> str:
    """datetime optional 轉 ISO 字串。"""

    return value.isoformat() if value else ""


def _optional_int(value: object) -> int | None:
    """轉 int；空值回 None。"""

    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _optional_bool(value: object) -> bool | None:
    """轉 bool；空值回 None。"""

    if value is None:
        return None
    return bool(value)


def _number_or_zero(value: object) -> float:
    """轉成數值；失敗時回 0。"""

    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    text = value.strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _tupleish(value: object) -> tuple[str, ...]:
    """將 list/tuple/set 轉成字串 tuple。"""

    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(str(item) for item in value if str(item or "").strip())

