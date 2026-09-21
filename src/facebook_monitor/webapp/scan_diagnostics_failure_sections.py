"""Failed scan 的安全診斷區塊。"""

from __future__ import annotations

from collections.abc import Mapping

from facebook_monitor.worker.failure_diagnostics import (
    validate_serialized_worker_failure_diagnostics,
)

_PAGE_GUARD_KEYS = (
    "detector",
    "detector_version",
    "classification",
    "facebook_host",
    "matched_heading",
    "matched_detail",
    "article_count",
    "stable_observation_count",
    "body_text_length",
    "url_kind",
)
def append_failure_diagnostics(lines: list[str], value: object) -> None:
    """輸出固定 allowlist 的 page guard 診斷，不接受 raw 文字或 URL。"""

    validated = validate_serialized_worker_failure_diagnostics(value)
    if validated is None:
        return
    for section, keys in (("page_guard", _PAGE_GUARD_KEYS),):
        guard = validated.get(section)
        if not isinstance(guard, Mapping):
            continue
        lines.append(f"failure_diagnostics.{section}:")
        _append_guard_fields(lines, guard, keys)


def _append_guard_fields(
    lines: list[str],
    guard: Mapping[object, object],
    keys: tuple[str, ...],
) -> None:
    """輸出 validator 已確認過的固定 guard 欄位。"""

    for key in keys:
        item = guard.get(key)
        if isinstance(item, (str, int, bool)) or item is None:
            lines.append(f"  {key}={item}")
