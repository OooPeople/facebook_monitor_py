"""Failed scan 的安全診斷區塊。"""

from __future__ import annotations

from collections.abc import Mapping

from facebook_monitor.worker.failure_diagnostics import (
    validate_serialized_worker_failure_diagnostics,
)

_PAGE_GUARD_V1_KEYS = (
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
_PAGE_GUARD_V2_KEYS = (
    "detector",
    "detector_version",
    "classification",
    "facebook_host",
    "matched_heading",
    "matched_detail",
    "heading_inside_feed",
    "detail_inside_feed",
    "heading_detail_local",
    "visible_feed_candidate_count",
    "stable_observation_count",
    "url_kind",
)


def append_failure_diagnostics(lines: list[str], value: object) -> None:
    """輸出固定 allowlist 的 page guard 診斷，不接受 raw 文字或 URL。"""

    validated = validate_serialized_worker_failure_diagnostics(value)
    if validated is None:
        return
    for section in ("page_guard",):
        guard = validated.get(section)
        if not isinstance(guard, Mapping):
            continue
        keys = _PAGE_GUARD_V2_KEYS if guard.get("detector_version") == 2 else _PAGE_GUARD_V1_KEYS
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
