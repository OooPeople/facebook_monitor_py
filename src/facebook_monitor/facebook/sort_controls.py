"""Facebook sort control helpers。

職責：集中保守排序調整語義，在掃描前嘗試把 group feed 切到偏好的
「新貼文」排序，並回傳可保存到 scan metadata 的診斷結果。
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import json
import re
import time
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
SORT_MENU_ROOT_SELECTOR = (
    '[role="menu"],[role="listbox"],[role="dialog"],[aria-modal="true"]'
)
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


def _js_literal(value: object) -> str:
    """將 Python 常數輸出成 JavaScript literal。"""

    return json.dumps(value, ensure_ascii=False)


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


def _with_sort_diagnostics(
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


def ensure_preferred_feed_sort(page: Any, *, enabled: bool) -> SortAdjustResult:
    """掃描前保守嘗試把 group feed 切到新貼文排序。"""

    if not enabled:
        return build_disabled_sort_adjust_result(FEED_SORT_NEWEST_LABEL)

    native_attempt = try_native_feed_sort_click(page)
    if native_attempt.result is not None:
        return native_attempt.result

    _record_sort_menu_recovery(
        native_attempt.diagnostics,
        _recover_sort_menu_before_fallback(page, native_attempt.diagnostics),
    )
    result = page.evaluate(FEED_SORT_ADJUST_SCRIPT, FEED_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=FEED_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


def ensure_preferred_comment_sort(page: Any, *, enabled: bool) -> SortAdjustResult:
    """掃描前保守嘗試把單篇貼文留言切到由新到舊。"""

    if not enabled:
        return build_disabled_sort_adjust_result(COMMENT_SORT_NEWEST_LABEL)

    native_attempt = try_native_comment_sort_click(page)
    if native_attempt.result is not None:
        return native_attempt.result

    _record_sort_menu_recovery(
        native_attempt.diagnostics,
        _recover_sort_menu_before_fallback(page, native_attempt.diagnostics),
    )
    result = page.evaluate(COMMENT_SORT_ADJUST_SCRIPT, COMMENT_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=COMMENT_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


async def ensure_preferred_feed_sort_async(page: Any, *, enabled: bool) -> SortAdjustResult:
    """resident main worker 掃描前嘗試把 group feed 切到新貼文排序。"""

    if not enabled:
        return build_disabled_sort_adjust_result(FEED_SORT_NEWEST_LABEL)

    native_attempt = await try_native_feed_sort_click_async(page)
    if native_attempt.result is not None:
        return native_attempt.result

    _record_sort_menu_recovery(
        native_attempt.diagnostics,
        await _recover_sort_menu_before_fallback_async(
            page,
            native_attempt.diagnostics,
        ),
    )
    result = await page.evaluate(FEED_SORT_ADJUST_SCRIPT, FEED_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=FEED_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


async def ensure_preferred_comment_sort_async(
    page: Any,
    *,
    enabled: bool,
) -> SortAdjustResult:
    """resident main worker 掃描前嘗試把留言切到由新到舊。"""

    if not enabled:
        return build_disabled_sort_adjust_result(COMMENT_SORT_NEWEST_LABEL)

    native_attempt = await try_native_comment_sort_click_async(page)
    if native_attempt.result is not None:
        return native_attempt.result

    _record_sort_menu_recovery(
        native_attempt.diagnostics,
        await _recover_sort_menu_before_fallback_async(
            page,
            native_attempt.diagnostics,
        ),
    )
    result = await page.evaluate(COMMENT_SORT_ADJUST_SCRIPT, COMMENT_SORT_NEWEST_LABEL)
    fallback_result = normalize_sort_adjust_result(
        result,
        preferred_label=COMMENT_SORT_NEWEST_LABEL,
    )
    return _with_fallback_diagnostics(fallback_result, native_attempt.diagnostics)


def _build_sort_result(
    *,
    spec: NativeSortSpec,
    before_label: str,
    after_label: str,
    diagnostics: dict[str, Any] | None = None,
) -> SortAdjustResult:
    """把 trusted click 結果整理成既有 sort diagnostics。"""

    return SortAdjustResult(
        attempted=True,
        changed=after_label == spec.preferred_label and before_label != after_label,
        preferred_label=spec.preferred_label,
        before_label=before_label,
        after_label=after_label,
        reason=(
            SORT_REASON_UPDATED_TO_PREFERRED_SORT
            if after_label == spec.preferred_label
            else SORT_REASON_SORT_UPDATE_UNCONFIRMED
        ),
        mutation_suppression_ms=SORT_MUTATION_SUPPRESSION_MS,
        mutation_suppression_reason=SORT_MUTATION_SUPPRESSION_REASON,
        diagnostics=diagnostics,
    )


def _build_already_preferred_sort_result(
    *,
    spec: NativeSortSpec,
    before_label: str,
    diagnostics: dict[str, Any],
) -> SortAdjustResult:
    """建立排序已是 preferred label 時的 native diagnostics。"""

    return SortAdjustResult(
        attempted=False,
        changed=False,
        preferred_label=spec.preferred_label,
        before_label=before_label,
        after_label=before_label,
        reason=SORT_REASON_ALREADY_PREFERRED_SORT,
        diagnostics=diagnostics,
    )


def _with_fallback_diagnostics(
    result: SortAdjustResult,
    native_diagnostics: dict[str, Any],
) -> SortAdjustResult:
    """JS fallback 結果併入 native 失敗階段，方便判讀 fallback 是否啟動。"""

    if not native_diagnostics:
        return result
    diagnostics = {
        **native_diagnostics,
        "method": SORT_METHOD_JS_FALLBACK,
        "fallback_used": True,
    }
    return _with_sort_diagnostics(result, diagnostics)


def _native_sort_base_diagnostics(spec: NativeSortSpec) -> dict[str, Any]:
    """建立 native trusted click 嘗試的共用 diagnostics。"""

    return {
        "method": SORT_METHOD_NATIVE_LOCATOR,
        "target_kind": spec.target_kind,
        "native_attempted": True,
        "confirm_timeout_ms": SORT_CONFIRM_TIMEOUT_MS,
    }


def try_native_feed_sort_click(page: Any) -> NativeSortAttempt:
    """優先用 Playwright trusted click 切 posts sort，失敗時交回 JS fallback。"""

    return _try_native_sort_click(
        page,
        NativeSortSpec(
            target_kind="posts",
            preferred_label=FEED_SORT_NEWEST_LABEL,
            labels=FEED_SORT_LABELS,
            current_label_script=FEED_SORT_CURRENT_LABEL_SCRIPT,
        ),
    )


def try_native_comment_sort_click(page: Any) -> NativeSortAttempt:
    """優先用 Playwright trusted click 切 comments sort，失敗時交回 JS fallback。"""

    return _try_native_sort_click(
        page,
        NativeSortSpec(
            target_kind="comments",
            preferred_label=COMMENT_SORT_NEWEST_LABEL,
            labels=COMMENT_SORT_LABELS,
            current_label_script=COMMENT_SORT_CURRENT_LABEL_SCRIPT,
        ),
    )


async def try_native_feed_sort_click_async(page: Any) -> NativeSortAttempt:
    """async resident main 使用的 posts sort native click path。"""

    return await _try_native_sort_click_async(
        page,
        NativeSortSpec(
            target_kind="posts",
            preferred_label=FEED_SORT_NEWEST_LABEL,
            labels=FEED_SORT_LABELS,
            current_label_script=FEED_SORT_CURRENT_LABEL_SCRIPT,
        ),
    )


async def try_native_comment_sort_click_async(page: Any) -> NativeSortAttempt:
    """async resident main 使用的 comments sort native click path。"""

    return await _try_native_sort_click_async(
        page,
        NativeSortSpec(
            target_kind="comments",
            preferred_label=COMMENT_SORT_NEWEST_LABEL,
            labels=COMMENT_SORT_LABELS,
            current_label_script=COMMENT_SORT_CURRENT_LABEL_SCRIPT,
        ),
    )


def _try_native_sort_click(page: Any, spec: NativeSortSpec) -> NativeSortAttempt:
    """使用 Playwright locator/trusted click 嘗試完成排序調整。"""

    if not _page_supports_native_sort_click(page):
        return NativeSortAttempt(result=None, diagnostics={})
    diagnostics = _native_sort_base_diagnostics(spec)
    try:
        before_label = str(page.evaluate(spec.current_label_script) or "")
    except Exception as exc:
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_CURRENT_LABEL,
                exc,
            ),
        )
    if before_label == spec.preferred_label:
        return NativeSortAttempt(
            result=_build_already_preferred_sort_result(
                spec=spec,
                before_label=before_label,
                diagnostics=diagnostics,
            ),
            diagnostics=diagnostics,
        )
    if before_label not in spec.labels:
        diagnostics["native_failure_stage"] = SORT_NATIVE_STAGE_CURRENT_LABEL
        diagnostics["native_after_label"] = before_label
        return NativeSortAttempt(result=None, diagnostics=diagnostics)
    try:
        control_info = _click_sort_control(page, before_label)
        diagnostics.update(control_info)
    except Exception as exc:
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_CLICK_CONTROL,
                exc,
            ),
        )
    try:
        option_info = _click_preferred_sort_option(page, spec.preferred_label)
        diagnostics.update(option_info)
    except Exception as exc:
        diagnostics.update(_sort_menu_snapshot_diagnostics(page, spec.preferred_label))
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_FIND_OPTION,
                exc,
            ),
        )
    try:
        after_label = _wait_for_sort_label(
            page,
            current_label_script=spec.current_label_script,
            preferred_label=spec.preferred_label,
        )
    except Exception as exc:
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_CONFIRM_LABEL,
                exc,
            ),
        )
    diagnostics["native_after_label"] = after_label
    if after_label != spec.preferred_label:
        diagnostics["native_failure_stage"] = SORT_NATIVE_STAGE_CONFIRM_LABEL
        return NativeSortAttempt(result=None, diagnostics=diagnostics)
    return NativeSortAttempt(
        result=_build_sort_result(
            spec=spec,
            before_label=before_label,
            after_label=after_label,
            diagnostics=diagnostics,
        ),
        diagnostics=diagnostics,
    )


async def _try_native_sort_click_async(page: Any, spec: NativeSortSpec) -> NativeSortAttempt:
    """async 版本的 Playwright locator/trusted click 排序調整。"""

    if not _page_supports_native_sort_click(page):
        return NativeSortAttempt(result=None, diagnostics={})
    diagnostics = _native_sort_base_diagnostics(spec)
    try:
        before_label = str(await page.evaluate(spec.current_label_script) or "")
    except Exception as exc:
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_CURRENT_LABEL,
                exc,
            ),
        )
    if before_label == spec.preferred_label:
        return NativeSortAttempt(
            result=_build_already_preferred_sort_result(
                spec=spec,
                before_label=before_label,
                diagnostics=diagnostics,
            ),
            diagnostics=diagnostics,
        )
    if before_label not in spec.labels:
        diagnostics["native_failure_stage"] = SORT_NATIVE_STAGE_CURRENT_LABEL
        diagnostics["native_after_label"] = before_label
        return NativeSortAttempt(result=None, diagnostics=diagnostics)
    try:
        control_info = await _click_sort_control_async(page, before_label)
        diagnostics.update(control_info)
    except Exception as exc:
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_CLICK_CONTROL,
                exc,
            ),
        )
    try:
        option_info = await _click_preferred_sort_option_async(page, spec.preferred_label)
        diagnostics.update(option_info)
    except Exception as exc:
        diagnostics.update(
            await _sort_menu_snapshot_diagnostics_async(page, spec.preferred_label)
        )
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_FIND_OPTION,
                exc,
            ),
        )
    try:
        after_label = await _wait_for_sort_label_async(
            page,
            current_label_script=spec.current_label_script,
            preferred_label=spec.preferred_label,
        )
    except Exception as exc:
        return NativeSortAttempt(
            result=None,
            diagnostics=_native_failure_diagnostics(
                diagnostics,
                SORT_NATIVE_STAGE_CONFIRM_LABEL,
                exc,
            ),
        )
    diagnostics["native_after_label"] = after_label
    if after_label != spec.preferred_label:
        diagnostics["native_failure_stage"] = SORT_NATIVE_STAGE_CONFIRM_LABEL
        return NativeSortAttempt(result=None, diagnostics=diagnostics)
    return NativeSortAttempt(
        result=_build_sort_result(
            spec=spec,
            before_label=before_label,
            after_label=after_label,
            diagnostics=diagnostics,
        ),
        diagnostics=diagnostics,
    )


def _page_supports_native_sort_click(page: Any) -> bool:
    """確認 page 至少有 locator trusted click 需要的 Playwright API。"""

    return all(
        hasattr(page, attr)
        for attr in ("evaluate", "get_by_role", "locator", "wait_for_timeout")
    )


def _native_failure_diagnostics(
    diagnostics: dict[str, Any],
    stage: str,
    exc: Exception,
) -> dict[str, Any]:
    """補上 native click 失敗階段與例外類型。"""

    updated = dict(diagnostics)
    updated["native_failure_stage"] = stage
    updated["native_exception_class"] = exc.__class__.__name__
    return updated


def _click_sort_control(page: Any, before_label: str) -> dict[str, Any]:
    """用 role/text locator 點開排序控制。"""

    return _click_first_locator(
        _sort_control_locators(page, before_label),
        stage=SORT_NATIVE_STAGE_CLICK_CONTROL,
    )


async def _click_sort_control_async(page: Any, before_label: str) -> dict[str, Any]:
    """async 版本：用 role/text locator 點開排序控制。"""

    return await _click_first_locator_async(
        _sort_control_locators(page, before_label),
        stage=SORT_NATIVE_STAGE_CLICK_CONTROL,
    )


def _click_preferred_sort_option(page: Any, preferred_label: str) -> dict[str, Any]:
    """在 menu scope 內優先點擊 preferred sort option。"""

    diagnostics = _menu_root_diagnostics(page, preferred_label)
    option_info = _click_first_locator(
        _sort_option_locators(page, preferred_label),
        stage=SORT_NATIVE_STAGE_FIND_OPTION,
        wait_timeout_ms=SORT_OPTION_WAIT_TIMEOUT_MS,
    )
    diagnostics.update(option_info)
    diagnostics["clicked_option_text"] = preferred_label
    if not diagnostics.get("menu_opened"):
        diagnostics["menu_opened"] = bool(option_info.get("preferred_option_count"))
    return diagnostics


async def _click_preferred_sort_option_async(
    page: Any,
    preferred_label: str,
) -> dict[str, Any]:
    """async 版本：在 menu scope 內優先點擊 preferred sort option。"""

    diagnostics = await _menu_root_diagnostics_async(page, preferred_label)
    option_info = await _click_first_locator_async(
        _sort_option_locators(page, preferred_label),
        stage=SORT_NATIVE_STAGE_FIND_OPTION,
        wait_timeout_ms=SORT_OPTION_WAIT_TIMEOUT_MS,
    )
    diagnostics.update(option_info)
    diagnostics["clicked_option_text"] = preferred_label
    if not diagnostics.get("menu_opened"):
        diagnostics["menu_opened"] = bool(option_info.get("preferred_option_count"))
    return diagnostics


def _sort_control_locators(page: Any, before_label: str) -> list[tuple[str, Any]]:
    """依可靠度排序 control locator。"""

    pattern = re.compile(re.escape(before_label))
    locators: list[tuple[str, Any]] = []
    locators.append(("role_button", page.get_by_role("button", name=pattern)))
    locators.append(
        (
            "aria_control",
            page.locator('[role="button"],[aria-haspopup="menu"],[aria-expanded]').filter(
                has_text=before_label
            ),
        )
    )
    return locators


def _sort_option_locators(page: Any, preferred_label: str) -> list[tuple[str, Any]]:
    """依可靠度排序 option locator，先 scoped 到 menu root 再退到 page。"""

    pattern = re.compile(re.escape(preferred_label))
    locators: list[tuple[str, Any]] = []
    menu_root = page.locator(SORT_MENU_ROOT_SELECTOR).filter(has_text=preferred_label)
    for role in SORT_OPTION_ROLES:
        locators.append((f"scoped_{role}", menu_root.get_by_role(role, name=pattern)))
    for role in SORT_OPTION_ROLES:
        locators.append((f"page_{role}", page.get_by_role(role, name=pattern)))
    return locators


def _click_first_locator(
    locators: list[tuple[str, Any]],
    *,
    stage: str,
    wait_timeout_ms: int = SORT_NATIVE_CLICK_TIMEOUT_MS,
) -> dict[str, Any]:
    """點擊第一個可用 locator，回傳候選數與 locator 名稱。"""

    last_error: Exception | None = None
    total_count = 0
    for locator_name, locator in locators:
        count = _safe_locator_count(locator)
        total_count += count
        try:
            first = locator.first
            if hasattr(first, "wait_for"):
                first.wait_for(state="visible", timeout=wait_timeout_ms)
            first.click(timeout=SORT_NATIVE_CLICK_TIMEOUT_MS)
            return _locator_click_diagnostics(stage, locator_name, max(count, 1))
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"no locator candidates for {stage}: {total_count}")


async def _click_first_locator_async(
    locators: list[tuple[str, Any]],
    *,
    stage: str,
    wait_timeout_ms: int = SORT_NATIVE_CLICK_TIMEOUT_MS,
) -> dict[str, Any]:
    """async 版本：點擊第一個可用 locator。"""

    last_error: Exception | None = None
    total_count = 0
    for locator_name, locator in locators:
        count = await _safe_locator_count_async(locator)
        total_count += count
        try:
            first = locator.first
            if hasattr(first, "wait_for"):
                await first.wait_for(state="visible", timeout=wait_timeout_ms)
            await first.click(timeout=SORT_NATIVE_CLICK_TIMEOUT_MS)
            return _locator_click_diagnostics(stage, locator_name, max(count, 1))
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"no locator candidates for {stage}: {total_count}")


def _locator_click_diagnostics(
    stage: str,
    locator_name: str,
    count: int,
) -> dict[str, Any]:
    """建立 locator 點擊成功的診斷欄位。"""

    if stage == SORT_NATIVE_STAGE_CLICK_CONTROL:
        return {
            "control_locator": locator_name,
            "control_candidate_count": count,
        }
    return {
        "option_locator": locator_name,
        "preferred_option_count": count,
    }


def _safe_locator_count(locator: Any) -> int:
    """安全取得 locator count；失敗時回傳 0 但不阻斷 click auto-wait。"""

    if not hasattr(locator, "count"):
        return 0
    try:
        return int(locator.count())
    except Exception:
        return 0


async def _safe_locator_count_async(locator: Any) -> int:
    """async 版本：安全取得 locator count。"""

    if not hasattr(locator, "count"):
        return 0
    try:
        return int(await locator.count())
    except Exception:
        return 0


def _should_recover_sort_menu_before_fallback(diagnostics: dict[str, Any]) -> bool:
    """判斷 native 失敗後是否可安全關閉殘留排序選單。"""

    if diagnostics.get("native_failure_stage") != SORT_NATIVE_STAGE_FIND_OPTION:
        return False
    return diagnostics.get("menu_opened") is True


def _record_sort_menu_recovery(
    diagnostics: dict[str, Any],
    recovered: bool,
) -> None:
    """把 fallback 前 recovery 結果寫回 native diagnostics。"""

    if recovered:
        diagnostics["fallback_recovery"] = "escape"


def _recover_sort_menu_before_fallback(
    page: Any,
    diagnostics: dict[str, Any],
) -> bool:
    """native option 失敗且殘留選單明確存在時，先關閉選單再 fallback。"""

    if not _should_recover_sort_menu_before_fallback(diagnostics):
        return False

    keyboard = getattr(page, "keyboard", None)
    if keyboard is None or not hasattr(keyboard, "press"):
        return False
    try:
        keyboard.press("Escape")
        if hasattr(page, "wait_for_timeout"):
            page.wait_for_timeout(SORT_OPTION_WAIT_INTERVAL_MS)
    except Exception:
        return False
    return True


async def _recover_sort_menu_before_fallback_async(
    page: Any,
    diagnostics: dict[str, Any],
) -> bool:
    """async 版本：native option 失敗且殘留選單明確存在時才關閉選單。"""

    if not _should_recover_sort_menu_before_fallback(diagnostics):
        return False

    keyboard = getattr(page, "keyboard", None)
    if keyboard is None or not hasattr(keyboard, "press"):
        return False
    try:
        await keyboard.press("Escape")
        if hasattr(page, "wait_for_timeout"):
            await page.wait_for_timeout(SORT_OPTION_WAIT_INTERVAL_MS)
    except Exception:
        return False
    return True


def _menu_root_diagnostics(page: Any, preferred_label: str) -> dict[str, Any]:
    """檢查 preferred option 所在 menu root 是否已可見。"""

    try:
        root = page.locator(SORT_MENU_ROOT_SELECTOR).filter(has_text=preferred_label)
        count = _safe_locator_count(root)
        if count > 0:
            return {"menu_opened": True, "menu_role": SORT_MENU_ROOT_SELECTOR}
    except Exception:
        pass
    return {"menu_opened": False}


def _sort_menu_snapshot_diagnostics(page: Any, preferred_label: str) -> dict[str, Any]:
    """native option 失敗時擷取選單與候選文字。"""

    diagnostics = _menu_root_diagnostics(page, preferred_label)
    candidate_texts = _collect_visible_sort_candidate_texts(page)
    if candidate_texts:
        diagnostics["menu_candidate_texts"] = candidate_texts
    return diagnostics


async def _menu_root_diagnostics_async(
    page: Any,
    preferred_label: str,
) -> dict[str, Any]:
    """async 版本：檢查 preferred option 所在 menu root 是否已可見。"""

    try:
        root = page.locator(SORT_MENU_ROOT_SELECTOR).filter(has_text=preferred_label)
        count = await _safe_locator_count_async(root)
        if count > 0:
            return {"menu_opened": True, "menu_role": SORT_MENU_ROOT_SELECTOR}
    except Exception:
        pass
    return {"menu_opened": False}


async def _sort_menu_snapshot_diagnostics_async(
    page: Any,
    preferred_label: str,
) -> dict[str, Any]:
    """async 版本：native option 失敗時擷取選單與候選文字。"""

    diagnostics = await _menu_root_diagnostics_async(page, preferred_label)
    candidate_texts = await _collect_visible_sort_candidate_texts_async(page)
    if candidate_texts:
        diagnostics["menu_candidate_texts"] = candidate_texts
    return diagnostics


SORT_MENU_CANDIDATE_TEXTS_SCRIPT = """
() => {
  const normalizeText = (value) => String(value || "").replace(/[\\u200B-\\u200D\\uFEFF]/g, "").replace(/\\s+/g, " ").trim();
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const selectors = [
    '[role="menu"]',
    '[role="listbox"]',
    '[role="dialog"]',
    '[aria-modal="true"]',
    '[role="menuitemradio"]',
    '[role="menuitemcheckbox"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="radio"]',
    '[role="button"]',
    '[aria-checked]',
    '[aria-selected]',
    'span[dir="auto"]',
  ];
  const texts = [];
  for (const selector of selectors) {
    for (const element of document.querySelectorAll(selector)) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (!text) continue;
      texts.push(text.slice(0, 160));
    }
  }
  return Array.from(new Set(texts)).slice(0, 30);
}
"""


def _collect_visible_sort_candidate_texts(page: Any) -> tuple[str, ...]:
    """擷取目前畫面可見排序候選文字，供 native 失敗診斷。"""

    try:
        result = page.evaluate(SORT_MENU_CANDIDATE_TEXTS_SCRIPT)
    except Exception:
        return ()
    if not isinstance(result, list):
        return ()
    return tuple(str(item) for item in result[:30] if str(item).strip())


async def _collect_visible_sort_candidate_texts_async(page: Any) -> tuple[str, ...]:
    """async 版本：擷取目前畫面可見排序候選文字。"""

    try:
        result = await page.evaluate(SORT_MENU_CANDIDATE_TEXTS_SCRIPT)
    except Exception:
        return ()
    if not isinstance(result, list):
        return ()
    return tuple(str(item) for item in result[:30] if str(item).strip())


def _wait_for_sort_label(
    page: Any,
    *,
    current_label_script: str,
    preferred_label: str,
) -> str:
    """狀態式等待目前排序 label 變成 preferred label。"""

    deadline = time.monotonic() + SORT_CONFIRM_TIMEOUT_MS / 1000
    last_label = ""
    while time.monotonic() <= deadline:
        last_label = str(page.evaluate(current_label_script) or "")
        if last_label == preferred_label:
            return last_label
        page.wait_for_timeout(SORT_CONFIRM_INTERVAL_MS)
    return last_label


async def _wait_for_sort_label_async(
    page: Any,
    *,
    current_label_script: str,
    preferred_label: str,
) -> str:
    """async 版本：狀態式等待目前排序 label 變成 preferred label。"""

    deadline = time.monotonic() + SORT_CONFIRM_TIMEOUT_MS / 1000
    last_label = ""
    while time.monotonic() <= deadline:
        last_label = str(await page.evaluate(current_label_script) or "")
        if last_label == preferred_label:
            return last_label
        await page.wait_for_timeout(SORT_CONFIRM_INTERVAL_MS)
    return last_label


COMMENT_SORT_CURRENT_LABEL_SCRIPT = """
() => {
  const labels = __COMMENT_SORT_LABELS__;
  const descriptionFragments = __COMMENT_SORT_DESCRIPTION_FRAGMENTS__;
  const normalizeText = (value) => String(value || "").replace(/[\\u200B-\\u200D\\uFEFF]/g, "").replace(/\\s+/g, " ").trim();
  const isGroupPostPermalinkPage = () => {
    if (location.hostname !== "www.facebook.com") return false;
    return /^\\/groups\\/[^/?#]+\\/(posts?|permalink)\\/[^/?#]+/i.test(location.pathname || "");
  };
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const extractKnownLabelFromText = (value, knownLabels = labels) => {
    const text = normalizeText(value);
    if (!text) return "";
    return knownLabels.find((label) => text.includes(label)) || "";
  };
  const isLikelyCommentSortOptionText = (value) => {
    const text = normalizeText(value);
    return descriptionFragments.some((fragment) => text.includes(fragment));
  };
  const findCommentSortLabelFromButtonText = (value) => {
    const text = normalizeText(value);
    if (!text || isLikelyCommentSortOptionText(text)) return "";
    return extractKnownLabelFromText(text, labels);
  };
  if (!isGroupPostPermalinkPage()) return "";
  const buttons = document.querySelectorAll('[role="button"], [aria-haspopup="menu"], [aria-expanded]');
  for (const candidate of Array.from(buttons || [])) {
    if (!(candidate instanceof HTMLElement)) continue;
    if (!isVisibleElement(candidate)) continue;
    const label = findCommentSortLabelFromButtonText(candidate.innerText || candidate.textContent || "");
    if (label) return label;
  }
  for (const span of document.querySelectorAll('span[dir="auto"]')) {
    if (!(span instanceof HTMLElement)) continue;
    if (!isVisibleElement(span)) continue;
    const text = normalizeText(span.innerText || span.textContent || "");
    if (!labels.includes(text)) continue;
    const control = span.closest('[role="button"], [aria-haspopup="menu"], [aria-expanded]');
    if (control instanceof HTMLElement) return text;
  }
  return "";
}
""".replace(
    "__COMMENT_SORT_LABELS__",
    _js_literal(COMMENT_SORT_LABELS),
).replace(
    "__COMMENT_SORT_DESCRIPTION_FRAGMENTS__",
    _js_literal(COMMENT_SORT_DESCRIPTION_FRAGMENTS),
)


FEED_SORT_CURRENT_LABEL_SCRIPT = """
() => {
  const labels = __FEED_SORT_LABELS__;
  const normalizeText = (value) => String(value || "").replace(/[\\u200B-\\u200D\\uFEFF]/g, "").replace(/\\s+/g, " ").trim();
  const getCurrentGroupId = () => {
    const match = (location.pathname || "").match(/^\\/groups\\/([^/?#]+)/i);
    return match ? match[1] : "";
  };
  const isSupportedGroupPage = () => location.hostname === "www.facebook.com" && Boolean(getCurrentGroupId());
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const extractKnownLabelFromText = (value) => {
    const text = normalizeText(value);
    if (!text) return "";
    return labels.find((label) => text.includes(label)) || "";
  };
  const findFeedSortLabelFromButtonText = (value) => {
    const text = normalizeText(value);
    if (!text || !text.includes("社團動態消息排序方式")) return "";
    return extractKnownLabelFromText(text);
  };
  if (!isSupportedGroupPage()) return "";
  for (const button of document.querySelectorAll('[role="button"]')) {
    if (!(button instanceof HTMLElement)) continue;
    if (!isVisibleElement(button)) continue;
    const heading = button.querySelector("h2");
    const headingText = normalizeText(heading?.innerText || heading?.textContent || "");
    if (headingText && labels.includes(headingText)) return headingText;
    const label = findFeedSortLabelFromButtonText(button.innerText || button.textContent || "");
    if (label) return label;
  }
  return "";
}
""".replace(
    "__FEED_SORT_LABELS__",
    _js_literal(FEED_SORT_LABELS),
)


FEED_SORT_ADJUST_SCRIPT = """
async (preferredLabel) => {
  const labels = __FEED_SORT_LABELS__;
  const normalizeText = (value) => String(value || "").replace(/[\\u200B-\\u200D\\uFEFF]/g, "").replace(/\\s+/g, " ").trim();
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const getCurrentGroupId = () => {
    const match = (location.pathname || "").match(/^\\/groups\\/([^/?#]+)/i);
    return match ? match[1] : "";
  };
  const isSupportedGroupPage = () => location.hostname === "www.facebook.com" && Boolean(getCurrentGroupId());
  const getCurrentScanTarget = () => ({
    kind: "posts",
    groupId: getCurrentGroupId(),
    supported: isSupportedGroupPage(),
  });
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const extractKnownLabelFromText = (value) => {
    const text = normalizeText(value);
    if (!text) return "";
    return labels.find((label) => text.includes(label)) || "";
  };
  const findFeedSortLabelFromButtonText = (value) => {
    const text = normalizeText(value);
    if (!text || !text.includes("社團動態消息排序方式")) return "";
    return extractKnownLabelFromText(text);
  };
  const getCurrentFeedSortControl = () => {
    if (!isSupportedGroupPage()) {
      return { label: "", control: null };
    }
    for (const button of document.querySelectorAll('[role="button"]')) {
      if (!(button instanceof HTMLElement)) continue;
      if (!isVisibleElement(button)) continue;
      const heading = button.querySelector("h2");
      const headingText = normalizeText(heading?.innerText || heading?.textContent || "");
      if (headingText && labels.includes(headingText)) {
        return { label: headingText, control: button };
      }
      const label = findFeedSortLabelFromButtonText(button.innerText || button.textContent || "");
      if (label) {
        return { label, control: button };
      }
    }
    return { label: "", control: null };
  };
  const getCurrentFeedSortLabel = () => getCurrentFeedSortControl().label || "";
  const clickFacebookControl = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    try {
      const eventInit = { bubbles: true, cancelable: true, composed: true, view: window };
      element.dispatchEvent(new MouseEvent("mousedown", eventInit));
      element.dispatchEvent(new MouseEvent("mouseup", eventInit));
    } catch (error) {
      // Fall through to native click.
    }
    if (typeof element.click === "function") {
      element.click();
      return true;
    }
    return false;
  };
  const getSortMenuOptionClickTarget = (element) => {
    if (!(element instanceof HTMLElement)) return null;
    return element.closest('[role="menuitemradio"],[role="menuitemcheckbox"],[role="menuitem"],[role="option"],[role="button"],[aria-checked],[aria-selected],[tabindex]') || element;
  };
  const getSelectorElementsByOrder = (selectors) => {
    const elements = [];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (element instanceof HTMLElement) elements.push(element);
      }
    }
    return elements;
  };
  const isSortMenuOptionForLabel = (element, label, options = {}) => {
    if (!(element instanceof HTMLElement)) return false;
    if (!isVisibleElement(element)) return false;
    const optionLabels = Array.isArray(options.labels) ? options.labels : [];
    const isDescriptionText = typeof options.isDescriptionText === "function"
      ? options.isDescriptionText
      : () => false;
    const text = normalizeText(element.innerText || element.textContent || "");
    if (!text || !text.includes(label)) return false;
    if (optionLabels.includes(text)) return true;
    return isDescriptionText(text);
  };
  const findSortMenuOption = (label, options = {}) => {
    const selectors = [
      '[role="menuitemradio"]',
      '[role="menuitemcheckbox"]',
      '[role="menuitem"]',
      '[role="option"]',
      '[aria-checked]',
      '[aria-selected]',
      '[role="button"]',
      'span[dir="auto"]',
    ];
    for (const element of getSelectorElementsByOrder(selectors)) {
      if (!isSortMenuOptionForLabel(element, label, options)) continue;
      const target = getSortMenuOptionClickTarget(element);
      if (target instanceof HTMLElement) return target;
    }
    return null;
  };
  const findFeedSortMenuOption = (label = __FEED_SORT_NEWEST_LABEL__) => findSortMenuOption(label, { labels });
  const collectVisibleSortCandidateTexts = () => {
    const selectors = [
      '[role="menuitemradio"]',
      '[role="menuitemcheckbox"]',
      '[role="menuitem"]',
      '[role="option"]',
      '[aria-checked]',
      '[aria-selected]',
      '[role="button"]',
      'span[dir="auto"]',
    ];
    const texts = [];
    for (const element of getSelectorElementsByOrder(selectors)) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (!text) continue;
      texts.push(text.slice(0, 160));
    }
    return Array.from(new Set(texts)).slice(0, 30);
  };
  const countVisibleMenuRootsForLabel = (label) => {
    let count = 0;
    for (const element of document.querySelectorAll('[role="menu"],[role="listbox"],[role="dialog"],[aria-modal="true"]')) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (text.includes(label)) count += 1;
    }
    return count;
  };
  const getPreferredSortLabelForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "posts" ? (preferredLabel || __FEED_SORT_NEWEST_LABEL__) : "";
  };
  const getCurrentSortControlForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "posts" ? getCurrentFeedSortControl() : { label: "", control: null };
  };
  const findPreferredSortMenuOptionForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return findFeedSortMenuOption(getPreferredSortLabelForScanTarget(scanTarget));
  };
  const waitForPreferredSortOptionForScanTarget = async (
    scanTarget = getCurrentScanTarget(),
    timeoutMs = __SORT_OPTION_WAIT_TIMEOUT_MS__,
    intervalMs = __SORT_OPTION_WAIT_INTERVAL_MS__,
  ) => {
    const deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0);
    while (Date.now() <= deadline) {
      const option = findPreferredSortMenuOptionForScanTarget(scanTarget);
      if (option instanceof HTMLElement) return option;
      await sleep(intervalMs);
    }
    return null;
  };
  const getCurrentScanSortLabel = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "posts" ? getCurrentFeedSortLabel() : "";
  };
  const suppressMutationsForMs = (ms, reason = "") => {
    window.__facebookMonitorMutationSuppression = {
      until: Date.now() + Math.max(0, Math.round(Number(ms) || 0)),
      reason: String(reason || ""),
    };
  };

  const scanTarget = getCurrentScanTarget();
  const preferredSortLabel = getPreferredSortLabelForScanTarget(scanTarget);
  if (!scanTarget?.supported) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: "",
      afterLabel: "",
      reason: __SORT_REASON_UNSUPPORTED_SCAN_TARGET__,
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "posts",
      failureStage: "route_check",
    };
  }

  const before = getCurrentSortControlForScanTarget(scanTarget);
  if (before.label === preferredSortLabel) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: before.label,
      reason: __SORT_REASON_ALREADY_PREFERRED_SORT__,
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "posts",
    };
  }
  if (!(before.control instanceof HTMLElement)) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: before.label,
      reason: __SORT_REASON_SORT_CONTROL_NOT_FOUND__,
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "posts",
      failureStage: "find_control",
    };
  }

  suppressMutationsForMs(__SORT_MUTATION_SUPPRESSION_MS__, __SORT_MUTATION_SUPPRESSION_REASON__);
  clickFacebookControl(before.control);
  const option = await waitForPreferredSortOptionForScanTarget(scanTarget);
  const menuRootCount = countVisibleMenuRootsForLabel(preferredSortLabel);
  if (!(option instanceof HTMLElement)) {
    return {
      attempted: true,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: getCurrentScanSortLabel(scanTarget),
      reason: __SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND__,
      mutationSuppressionMs: __SORT_MUTATION_SUPPRESSION_MS__,
      mutationSuppressionReason: __SORT_MUTATION_SUPPRESSION_REASON__,
      menuCandidateTexts: collectVisibleSortCandidateTexts(),
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "posts",
      failureStage: "find_option",
      menuOpened: menuRootCount > 0,
      preferredOptionCount: 0,
    };
  }
  clickFacebookControl(option);
  await sleep(900);
  const afterLabel = getCurrentScanSortLabel(scanTarget);
  return {
    attempted: true,
    changed: afterLabel === preferredSortLabel && before.label !== afterLabel,
    preferredLabel: preferredSortLabel,
    beforeLabel: before.label,
    afterLabel,
    reason: afterLabel === preferredSortLabel ? __SORT_REASON_UPDATED_TO_PREFERRED_SORT__ : __SORT_REASON_SORT_UPDATE_UNCONFIRMED__,
    mutationSuppressionMs: __SORT_MUTATION_SUPPRESSION_MS__,
    mutationSuppressionReason: __SORT_MUTATION_SUPPRESSION_REASON__,
    method: __SORT_METHOD_JS_FALLBACK__,
    targetKind: "posts",
    failureStage: afterLabel === preferredSortLabel ? "" : "confirm_label",
    menuOpened: menuRootCount > 0,
    preferredOptionCount: 1,
    clickedOptionText: preferredSortLabel,
  };
}
""".replace(
    "__FEED_SORT_LABELS__",
    _js_literal(FEED_SORT_LABELS),
).replace(
    "__FEED_SORT_NEWEST_LABEL__",
    _js_literal(FEED_SORT_NEWEST_LABEL),
).replace(
    "__SORT_REASON_UNSUPPORTED_SCAN_TARGET__",
    _js_literal(SORT_REASON_UNSUPPORTED_SCAN_TARGET),
).replace(
    "__SORT_REASON_ALREADY_PREFERRED_SORT__",
    _js_literal(SORT_REASON_ALREADY_PREFERRED_SORT),
).replace(
    "__SORT_REASON_SORT_CONTROL_NOT_FOUND__",
    _js_literal(SORT_REASON_SORT_CONTROL_NOT_FOUND),
).replace(
    "__SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND__",
    _js_literal(SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND),
).replace(
    "__SORT_REASON_UPDATED_TO_PREFERRED_SORT__",
    _js_literal(SORT_REASON_UPDATED_TO_PREFERRED_SORT),
).replace(
    "__SORT_REASON_SORT_UPDATE_UNCONFIRMED__",
    _js_literal(SORT_REASON_SORT_UPDATE_UNCONFIRMED),
).replace(
    "__SORT_MUTATION_SUPPRESSION_MS__",
    str(SORT_MUTATION_SUPPRESSION_MS),
).replace(
    "__SORT_MUTATION_SUPPRESSION_REASON__",
    _js_literal(SORT_MUTATION_SUPPRESSION_REASON),
).replace(
    "__SORT_OPTION_WAIT_TIMEOUT_MS__",
    str(SORT_OPTION_WAIT_TIMEOUT_MS),
).replace(
    "__SORT_OPTION_WAIT_INTERVAL_MS__",
    str(SORT_OPTION_WAIT_INTERVAL_MS),
).replace(
    "__SORT_METHOD_JS_FALLBACK__",
    _js_literal(SORT_METHOD_JS_FALLBACK),
)


COMMENT_SORT_ADJUST_SCRIPT = """
async (preferredLabel) => {
  const labels = __COMMENT_SORT_LABELS__;
  const descriptionFragments = __COMMENT_SORT_DESCRIPTION_FRAGMENTS__;
  const normalizeText = (value) => String(value || "").replace(/[\\u200B-\\u200D\\uFEFF]/g, "").replace(/\\s+/g, " ").trim();
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isGroupPostPermalinkPage = () => {
    if (location.hostname !== "www.facebook.com") return false;
    return /^\\/groups\\/[^/?#]+\\/(posts?|permalink)\\/[^/?#]+/i.test(location.pathname || "");
  };
  const getCurrentScanTarget = () => ({
    kind: "comments",
    supported: isGroupPostPermalinkPage(),
  });
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const extractKnownLabelFromText = (value, knownLabels = labels) => {
    const text = normalizeText(value);
    if (!text) return "";
    return knownLabels.find((label) => text.includes(label)) || "";
  };
  const isLikelyCommentSortOptionText = (value) => {
    const text = normalizeText(value);
    return descriptionFragments.some((fragment) => text.includes(fragment));
  };
  const findCommentSortLabelFromButtonText = (value) => {
    const text = normalizeText(value);
    if (!text || isLikelyCommentSortOptionText(text)) return "";
    return extractKnownLabelFromText(text, labels);
  };
  const getCommentSortControlFromCandidates = (candidates) => {
    for (const candidate of Array.from(candidates || [])) {
      if (!(candidate instanceof HTMLElement)) continue;
      if (!isVisibleElement(candidate)) continue;
      const label = findCommentSortLabelFromButtonText(candidate.innerText || candidate.textContent || "");
      if (label) return { label, control: candidate };
    }
    return { label: "", control: null };
  };
  const getCurrentCommentSortControl = () => {
    if (!isGroupPostPermalinkPage()) return { label: "", control: null };
    const buttons = document.querySelectorAll('[role="button"], [aria-haspopup="menu"], [aria-expanded]');
    const buttonResult = getCommentSortControlFromCandidates(buttons);
    if (buttonResult.label) return buttonResult;
    for (const span of document.querySelectorAll('span[dir="auto"]')) {
      if (!(span instanceof HTMLElement)) continue;
      if (!isVisibleElement(span)) continue;
      const text = normalizeText(span.innerText || span.textContent || "");
      if (!labels.includes(text)) continue;
      const control = span.closest('[role="button"], [aria-haspopup="menu"], [aria-expanded]');
      if (control instanceof HTMLElement) return { label: text, control };
    }
    return { label: "", control: null };
  };
  const getCurrentCommentSortLabel = () => getCurrentCommentSortControl().label || "";
  const clickFacebookControl = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    try {
      const eventInit = { bubbles: true, cancelable: true, composed: true, view: window };
      element.dispatchEvent(new MouseEvent("mousedown", eventInit));
      element.dispatchEvent(new MouseEvent("mouseup", eventInit));
    } catch (error) {
      // Fall through to native click.
    }
    if (typeof element.click === "function") {
      element.click();
      return true;
    }
    return false;
  };
  const getSortMenuOptionClickTarget = (element) => {
    if (!(element instanceof HTMLElement)) return null;
    return element.closest('[role="menuitemradio"],[role="menuitemcheckbox"],[role="menuitem"],[role="option"],[role="button"],[aria-checked],[aria-selected],[tabindex]') || element;
  };
  const getSelectorElementsByOrder = (selectors) => {
    const elements = [];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (element instanceof HTMLElement) elements.push(element);
      }
    }
    return elements;
  };
  const isSortMenuOptionForLabel = (element, label, options = {}) => {
    if (!(element instanceof HTMLElement)) return false;
    if (!isVisibleElement(element)) return false;
    const optionLabels = Array.isArray(options.labels) ? options.labels : [];
    const isDescriptionText = typeof options.isDescriptionText === "function"
      ? options.isDescriptionText
      : () => false;
    const text = normalizeText(element.innerText || element.textContent || "");
    if (!text || !text.includes(label)) return false;
    if (optionLabels.includes(text)) return true;
    const role = element.getAttribute("role") || "";
    const isMenuLike =
      role === "menuitemradio" ||
      role === "menuitemcheckbox" ||
      role === "menuitem" ||
      role === "option" ||
      element.hasAttribute("aria-checked") ||
      element.hasAttribute("aria-selected") ||
      element.closest('[role="menuitemradio"],[role="menuitemcheckbox"],[role="menuitem"],[role="option"],[aria-checked],[aria-selected]');
    if (isMenuLike) return true;
    return isDescriptionText(text);
  };
  const findCommentSortMenuOption = (label = __COMMENT_SORT_NEWEST_LABEL__) => {
    const selectors = [
      '[role="menuitemradio"]',
      '[role="menuitemcheckbox"]',
      '[role="menuitem"]',
      '[role="option"]',
      '[aria-checked]',
      '[aria-selected]',
      '[role="button"]',
      'span[dir="auto"]',
    ];
    for (const element of getSelectorElementsByOrder(selectors)) {
      if (!isSortMenuOptionForLabel(element, label, { labels, isDescriptionText: isLikelyCommentSortOptionText })) continue;
      const target = getSortMenuOptionClickTarget(element);
      if (target instanceof HTMLElement) return target;
    }
    return null;
  };
  const collectVisibleSortCandidateTexts = () => {
    const selectors = [
      '[role="menuitemradio"]',
      '[role="menuitemcheckbox"]',
      '[role="menuitem"]',
      '[role="option"]',
      '[aria-checked]',
      '[aria-selected]',
      '[role="button"]',
      'span[dir="auto"]',
    ];
    const texts = [];
    for (const element of getSelectorElementsByOrder(selectors)) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (!text) continue;
      texts.push(text.slice(0, 160));
    }
    return Array.from(new Set(texts)).slice(0, 30);
  };
  const countVisibleMenuRootsForLabel = (label) => {
    let count = 0;
    for (const element of document.querySelectorAll('[role="menu"],[role="listbox"],[role="dialog"],[aria-modal="true"]')) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (text.includes(label)) count += 1;
    }
    return count;
  };
  const getPreferredSortLabelForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? (preferredLabel || __COMMENT_SORT_NEWEST_LABEL__) : "";
  };
  const getCurrentSortControlForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? getCurrentCommentSortControl() : { label: "", control: null };
  };
  const findPreferredSortMenuOptionForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? findCommentSortMenuOption(getPreferredSortLabelForScanTarget(scanTarget)) : null;
  };
  const waitForPreferredSortOptionForScanTarget = async (
    scanTarget = getCurrentScanTarget(),
    timeoutMs = __COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS__,
    intervalMs = __COMMENT_SORT_OPTION_WAIT_INTERVAL_MS__,
  ) => {
    const deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0);
    while (Date.now() <= deadline) {
      const option = findPreferredSortMenuOptionForScanTarget(scanTarget);
      if (option instanceof HTMLElement) return option;
      await sleep(intervalMs);
    }
    return null;
  };
  const getCurrentScanSortLabel = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? getCurrentCommentSortLabel() : "";
  };
  const suppressMutationsForMs = (ms, reason = "") => {
    window.__facebookMonitorMutationSuppression = {
      until: Date.now() + Math.max(0, Math.round(Number(ms) || 0)),
      reason: String(reason || ""),
    };
  };

  const scanTarget = getCurrentScanTarget();
  const preferredSortLabel = getPreferredSortLabelForScanTarget(scanTarget);
  if (!scanTarget?.supported) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: "",
      afterLabel: "",
      reason: __SORT_REASON_UNSUPPORTED_SCAN_TARGET__,
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "comments",
      failureStage: "route_check",
    };
  }

  const before = getCurrentSortControlForScanTarget(scanTarget);
  if (before.label === preferredSortLabel) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: before.label,
      reason: __SORT_REASON_ALREADY_PREFERRED_SORT__,
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "comments",
    };
  }
  if (!(before.control instanceof HTMLElement)) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: before.label,
      reason: __SORT_REASON_SORT_CONTROL_NOT_FOUND__,
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "comments",
      failureStage: "find_control",
    };
  }

  suppressMutationsForMs(__SORT_MUTATION_SUPPRESSION_MS__, __SORT_MUTATION_SUPPRESSION_REASON__);
  clickFacebookControl(before.control);
  const option = await waitForPreferredSortOptionForScanTarget(scanTarget);
  const menuRootCount = countVisibleMenuRootsForLabel(preferredSortLabel);
  if (!(option instanceof HTMLElement)) {
    return {
      attempted: true,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: getCurrentScanSortLabel(scanTarget),
      reason: __SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND__,
      mutationSuppressionMs: __SORT_MUTATION_SUPPRESSION_MS__,
      mutationSuppressionReason: __SORT_MUTATION_SUPPRESSION_REASON__,
      menuCandidateTexts: collectVisibleSortCandidateTexts(),
      method: __SORT_METHOD_JS_FALLBACK__,
      targetKind: "comments",
      failureStage: "find_option",
      menuOpened: menuRootCount > 0,
      preferredOptionCount: 0,
    };
  }
  clickFacebookControl(option);
  await sleep(900);
  const afterLabel = getCurrentScanSortLabel(scanTarget);
  return {
    attempted: true,
    changed: afterLabel === preferredSortLabel && before.label !== afterLabel,
    preferredLabel: preferredSortLabel,
    beforeLabel: before.label,
    afterLabel,
    reason: afterLabel === preferredSortLabel ? __SORT_REASON_UPDATED_TO_PREFERRED_SORT__ : __SORT_REASON_SORT_UPDATE_UNCONFIRMED__,
    mutationSuppressionMs: __SORT_MUTATION_SUPPRESSION_MS__,
    mutationSuppressionReason: __SORT_MUTATION_SUPPRESSION_REASON__,
    method: __SORT_METHOD_JS_FALLBACK__,
    targetKind: "comments",
    failureStage: afterLabel === preferredSortLabel ? "" : "confirm_label",
    menuOpened: menuRootCount > 0,
    preferredOptionCount: 1,
    clickedOptionText: preferredSortLabel,
  };
}
""".replace(
    "__COMMENT_SORT_LABELS__",
    _js_literal(COMMENT_SORT_LABELS),
).replace(
    "__COMMENT_SORT_DESCRIPTION_FRAGMENTS__",
    _js_literal(COMMENT_SORT_DESCRIPTION_FRAGMENTS),
).replace(
    "__COMMENT_SORT_NEWEST_LABEL__",
    _js_literal(COMMENT_SORT_NEWEST_LABEL),
).replace(
    "__COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS__",
    str(COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS),
).replace(
    "__COMMENT_SORT_OPTION_WAIT_INTERVAL_MS__",
    str(COMMENT_SORT_OPTION_WAIT_INTERVAL_MS),
).replace(
    "__SORT_REASON_UNSUPPORTED_SCAN_TARGET__",
    _js_literal(SORT_REASON_UNSUPPORTED_SCAN_TARGET),
).replace(
    "__SORT_REASON_ALREADY_PREFERRED_SORT__",
    _js_literal(SORT_REASON_ALREADY_PREFERRED_SORT),
).replace(
    "__SORT_REASON_SORT_CONTROL_NOT_FOUND__",
    _js_literal(SORT_REASON_SORT_CONTROL_NOT_FOUND),
).replace(
    "__SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND__",
    _js_literal(SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND),
).replace(
    "__SORT_REASON_UPDATED_TO_PREFERRED_SORT__",
    _js_literal(SORT_REASON_UPDATED_TO_PREFERRED_SORT),
).replace(
    "__SORT_REASON_SORT_UPDATE_UNCONFIRMED__",
    _js_literal(SORT_REASON_SORT_UPDATE_UNCONFIRMED),
).replace(
    "__SORT_MUTATION_SUPPRESSION_MS__",
    str(SORT_MUTATION_SUPPRESSION_MS),
).replace(
    "__SORT_MUTATION_SUPPRESSION_REASON__",
    _js_literal(SORT_MUTATION_SUPPRESSION_REASON),
).replace(
    "__SORT_METHOD_JS_FALLBACK__",
    _js_literal(SORT_METHOD_JS_FALLBACK),
)
