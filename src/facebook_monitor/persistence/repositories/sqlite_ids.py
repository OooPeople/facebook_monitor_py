"""SQLite row id helpers。"""

from __future__ import annotations


def require_lastrowid(value: int | None, *, table_name: str) -> int:
    """回傳 insert row id；SQLite 未提供時視為 repository 寫入失敗。"""

    if value is None:
        raise RuntimeError(f"{table_name} insert did not return a row id")
    return int(value)
