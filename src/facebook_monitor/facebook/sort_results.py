"""Facebook sort adjustment result models。

職責：保存排序調整的穩定 metadata shape 與 diagnostics normalization，讓
DOM/native click orchestration 留在 `sort_runtime.py` / `sort_native_click.py`。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from typing import Any


FEED_SORT_NEWEST_LABEL = "新貼文"
FEED_SORT_LABELS = (FEED_SORT_NEWEST_LABEL, "最相關", "最新動態")
COMMENT_SORT_NEWEST_LABEL = "由新到舊"
COMMENT_SORT_LABELS = (COMMENT_SORT_NEWEST_LABEL, "最相關", "所有留言")
COMMENT_SORT_DESCRIPTION_FRAGMENTS = (
    "顯示所有留言",
    "最新的留言顯示在最上方",
    "優先顯示朋友的留言",
    "獲得最多互動的留言",
    "可能是垃圾訊息",
)
SORT_REASON_UNSUPPORTED_SCAN_TARGET = "unsupported_scan_target"
SORT_REASON_ALREADY_PREFERRED_SORT = "already_preferred_sort"
SORT_REASON_SORT_CONTROL_NOT_FOUND = "sort_control_not_found"
SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND = "preferred_sort_option_not_found"
SORT_REASON_UPDATED_TO_PREFERRED_SORT = "updated_to_preferred_sort"
SORT_REASON_SORT_UPDATE_UNCONFIRMED = "sort_update_unconfirmed"
SORT_REASON_AUTO_ADJUST_DISABLED = "auto_adjust_sort_disabled"
SORT_REASON_RESULT_INVALID = "sort_adjust_result_invalid"
SORT_MUTATION_SUPPRESSION_MS = 3200
SORT_MUTATION_SUPPRESSION_REASON = "auto_adjust_sort"
SORT_OPTION_WAIT_TIMEOUT_MS = 1800
SORT_OPTION_WAIT_INTERVAL_MS = 120
SORT_CONFIRM_TIMEOUT_MS = 2500
SORT_CONFIRM_INTERVAL_MS = 120
SORT_NATIVE_CLICK_TIMEOUT_MS = 3000
SORT_METHOD_NATIVE_LOCATOR = "native_locator"
SORT_METHOD_JS_FALLBACK = "js_fallback"
SORT_NATIVE_STAGE_CURRENT_LABEL = "current_label"
SORT_NATIVE_STAGE_CLICK_CONTROL = "click_control"
SORT_NATIVE_STAGE_FIND_OPTION = "find_option"
SORT_NATIVE_STAGE_CONFIRM_LABEL = "confirm_label"
SORT_MENU_ROOT_SELECTOR = '[role="menu"],[role="listbox"]'
SORT_OPTION_ROLES = (
    "menuitemradio",
    "menuitemcheckbox",
    "menuitem",
    "option",
    "radio",
    "checkbox",
    "button",
)
COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS = 1800
COMMENT_SORT_OPTION_WAIT_INTERVAL_MS = 120
SORT_DIAGNOSTIC_FIELD_ALIASES = {
    "method": "method",
    "targetKind": "target_kind",
    "target_kind": "target_kind",
    "failureStage": "failure_stage",
    "failure_stage": "failure_stage",
    "fallbackUsed": "fallback_used",
    "fallback_used": "fallback_used",
    "nativeAttempted": "native_attempted",
    "native_attempted": "native_attempted",
    "nativeFailureStage": "native_failure_stage",
    "native_failure_stage": "native_failure_stage",
    "nativeExceptionClass": "native_exception_class",
    "native_exception_class": "native_exception_class",
    "nativeAfterLabel": "native_after_label",
    "native_after_label": "native_after_label",
    "fallbackRecovery": "fallback_recovery",
    "fallback_recovery": "fallback_recovery",
    "controlCandidateCount": "control_candidate_count",
    "control_candidate_count": "control_candidate_count",
    "controlLocator": "control_locator",
    "control_locator": "control_locator",
    "menuOpened": "menu_opened",
    "menu_opened": "menu_opened",
    "menuRole": "menu_role",
    "menu_role": "menu_role",
    "preferredOptionCount": "preferred_option_count",
    "preferred_option_count": "preferred_option_count",
    "clickedOptionText": "clicked_option_text",
    "clicked_option_text": "clicked_option_text",
    "optionLocator": "option_locator",
    "option_locator": "option_locator",
    "confirmTimeoutMs": "confirm_timeout_ms",
    "confirm_timeout_ms": "confirm_timeout_ms",
}


@dataclass(frozen=True)
class SortAdjustResult:
    """保存一次排序調整嘗試結果。"""

    attempted: bool
    changed: bool
    preferred_label: str = FEED_SORT_NEWEST_LABEL
    before_label: str = ""
    after_label: str = ""
    reason: str = ""
    mutation_suppression_ms: int = 0
    mutation_suppression_reason: str = ""
    menu_candidate_texts: tuple[str, ...] = ()
    diagnostics: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        """轉成 scan metadata 使用的穩定欄位。"""

        metadata = {
            "attempted": self.attempted,
            "changed": self.changed,
            "preferred_label": self.preferred_label,
            "before_label": self.before_label,
            "after_label": self.after_label,
            "reason": self.reason,
            "mutation_suppression_ms": self.mutation_suppression_ms,
            "mutation_suppression_reason": self.mutation_suppression_reason,
            "menu_candidate_texts": list(self.menu_candidate_texts),
        }
        for key, value in (self.diagnostics or {}).items():
            if _is_empty_diagnostic_value(value):
                continue
            metadata[key] = list(value) if isinstance(value, tuple) else value
        return metadata


@dataclass(frozen=True)
class NativeSortSpec:
    """定義 posts/comments native click 排序調整的 target-specific 差異。"""

    target_kind: str
    preferred_label: str
    labels: tuple[str, ...]
    current_label_script: str


@dataclass(frozen=True)
class NativeSortAttempt:
    """保存 native trusted click 嘗試結果與可併入 fallback 的診斷。"""

    result: SortAdjustResult | None
    diagnostics: dict[str, Any]


def _is_empty_diagnostic_value(value: object) -> bool:
    """判斷 diagnostics 欄位是否應省略，保留 False 這類有意義狀態。"""

    return value is None or value == "" or value == () or value == [] or value == {}


def _coerce_sort_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """從 JS/native payload 擷取允許寫入 metadata 的附加診斷欄位。"""

    diagnostics: dict[str, Any] = {}
    for raw_key, key in SORT_DIAGNOSTIC_FIELD_ALIASES.items():
        if raw_key not in result:
            continue
        diagnostics[key] = _normalize_diagnostic_value(result[raw_key])
    return diagnostics


def _normalize_diagnostic_value(value: object) -> object:
    """把 diagnostics value 正規化成 JSON-friendly 型別。"""

    if isinstance(value, list | tuple):
        return [str(item) for item in value[:30]]
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    return str(value)


def with_sort_diagnostics(
    result: SortAdjustResult,
    diagnostics: dict[str, Any],
) -> SortAdjustResult:
    """回傳附加 diagnostics 的 SortAdjustResult，保留 result 自帶欄位優先權。"""

    merged = dict(diagnostics)
    merged.update(result.diagnostics or {})
    return replace(result, diagnostics=merged or None)


def build_disabled_sort_adjust_result(preferred_label: str) -> SortAdjustResult:
    """建立 auto_adjust_sort 關閉時的標準診斷結果。"""

    return SortAdjustResult(
        attempted=False,
        changed=False,
        preferred_label=preferred_label,
        reason=SORT_REASON_AUTO_ADJUST_DISABLED,
    )


def normalize_sort_adjust_result(result: object, *, preferred_label: str) -> SortAdjustResult:
    """將 Playwright evaluate 回傳值整理成穩定 SortAdjustResult。"""

    if not isinstance(result, dict):
        return SortAdjustResult(
            attempted=False,
            changed=False,
            preferred_label=preferred_label,
            reason=SORT_REASON_RESULT_INVALID,
        )
    raw_menu_candidate_texts = result.get("menuCandidateTexts")
    if isinstance(raw_menu_candidate_texts, list):
        menu_candidate_texts = tuple(str(item) for item in raw_menu_candidate_texts[:30])
    else:
        menu_candidate_texts = ()
    return SortAdjustResult(
        attempted=bool(result.get("attempted")),
        changed=bool(result.get("changed")),
        preferred_label=str(result.get("preferredLabel") or preferred_label),
        before_label=str(result.get("beforeLabel") or ""),
        after_label=str(result.get("afterLabel") or ""),
        reason=str(result.get("reason") or ""),
        mutation_suppression_ms=int(result.get("mutationSuppressionMs") or 0),
        mutation_suppression_reason=str(result.get("mutationSuppressionReason") or ""),
        menu_candidate_texts=menu_candidate_texts,
        diagnostics=_coerce_sort_diagnostics(result),
    )
