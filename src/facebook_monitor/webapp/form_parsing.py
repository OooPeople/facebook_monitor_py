"""Web UI form parsing helpers。"""

from __future__ import annotations


def checkbox_checked(value: str | None) -> bool:
    """解析 HTML checkbox 欄位。"""

    return value == "on"


def checkbox_payload(value: object) -> str | None:
    """將 JSON payload 的 truthy 值轉成 HTML checkbox 表示。"""

    return "on" if bool(value) else None


def int_payload(value: object, fallback: int) -> int:
    """解析 JSON payload 整數欄位，失敗時回傳既有預設。"""

    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback


__all__ = [
    "checkbox_checked",
    "checkbox_payload",
    "int_payload",
]
