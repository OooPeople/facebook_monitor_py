"""Scan diagnostics round list formatter。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def append_rounds(
    lines: list[str],
    label: str,
    value: object,
    formatter: Callable[[dict[str, Any]], str],
) -> None:
    """附加每輪 extractor diagnostics。"""

    if not isinstance(value, list) or not value:
        return
    lines.extend(["", f"{label}:"])
    for round_item in value:
        if isinstance(round_item, dict):
            lines.append(formatter(round_item))
