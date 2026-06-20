"""Scan diagnostics sort section formatter。"""

from __future__ import annotations

from facebook_monitor.webapp.scan_diagnostics_values import format_diagnostic_value
from facebook_monitor.webapp.scan_diagnostics_values import is_empty_diagnostic_value


_SORT_DIAGNOSTIC_KEYS = (
    "method",
    "target_kind",
    "fallback_used",
    "fallback_recovery",
    "failure_stage",
    "native_attempted",
    "native_failure_stage",
    "native_exception_class",
    "native_after_label",
    "control_candidate_count",
    "control_locator",
    "menu_opened",
    "menu_role",
    "preferred_option_count",
    "option_locator",
    "clicked_option_text",
    "confirm_timeout_ms",
)


def append_sort_diagnostics_block(
    lines: list[str],
    label: str,
    value: object,
) -> None:
    """附加 feed/comment sort diagnostics。"""

    if not isinstance(value, dict):
        return
    lines.extend(
        [
            "",
            f"{label}:",
            f"attempted={value.get('attempted', False)}",
            f"changed={value.get('changed', False)}",
            f"preferred_label={value.get('preferred_label', '')}",
            f"before_label={value.get('before_label', '')}",
            f"after_label={value.get('after_label', '')}",
            f"reason={value.get('reason', '')}",
            f"mutation_suppression_ms={value.get('mutation_suppression_ms', 0)}",
            f"mutation_suppression_reason={value.get('mutation_suppression_reason', '')}",
        ]
    )
    menu_candidate_texts = value.get("menu_candidate_texts")
    if menu_candidate_texts:
        menu_candidate_text = format_diagnostic_value(menu_candidate_texts)
        lines.append(f"menu_candidate_texts={menu_candidate_text}")
    for key in _SORT_DIAGNOSTIC_KEYS:
        if key not in value:
            continue
        if is_empty_diagnostic_value(value[key]):
            continue
        formatted_value = format_diagnostic_value(value[key])
        lines.append(f"{key}={formatted_value}")
