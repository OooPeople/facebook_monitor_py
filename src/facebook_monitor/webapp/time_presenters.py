"""Web UI 共用時間格式化 helper。"""

from __future__ import annotations

from datetime import datetime


def format_datetime_for_ui(value: datetime) -> str:
    """將時間轉成本機時區的短格式供 UI 顯示。"""

    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_optional_datetime_for_ui(value: datetime | None) -> str:
    """將可為空的時間轉成 UI 診斷文字。"""

    if value is None:
        return "(none)"
    return format_datetime_for_ui(value)
