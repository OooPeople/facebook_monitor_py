"""Scan diagnostics 文字輸出的共用值格式化。"""

from __future__ import annotations

import json


def format_diagnostic_value(value: object) -> str:
    """將巢狀 debug 值轉成穩定 JSON，方便複製給 review。"""

    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def is_empty_diagnostic_value(value: object) -> bool:
    """判斷 diagnostics 欄位是否應從日常輸出省略。"""

    return value is None or value == "" or value == [] or value == {}
