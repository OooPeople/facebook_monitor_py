"""Playwright native click sort adjustment。

職責：執行 auto_adjust_sort 的 trusted click state machine，保留 sync/async
平行流程與既有 diagnostics 語義。
"""

from __future__ import annotations

import time
from typing import Any

from facebook_monitor.facebook.sort_menu_diagnostics import menu_root_diagnostics
from facebook_monitor.facebook.sort_menu_diagnostics import menu_root_diagnostics_async
from facebook_monitor.facebook.sort_menu_diagnostics import sort_menu_snapshot_diagnostics
from facebook_monitor.facebook.sort_menu_diagnostics import (
    sort_menu_snapshot_diagnostics_async,
)
from facebook_monitor.facebook.sort_native_locators import click_first_locator
from facebook_monitor.facebook.sort_native_locators import click_first_locator_async
from facebook_monitor.facebook.sort_native_locators import page_supports_native_sort_click
from facebook_monitor.facebook.sort_native_locators import sort_control_locators
from facebook_monitor.facebook.sort_native_locators import sort_option_locators
from facebook_monitor.facebook.sort_results import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import FEED_SORT_LABELS
from facebook_monitor.facebook.sort_results import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import NativeSortAttempt
from facebook_monitor.facebook.sort_results import NativeSortSpec
from facebook_monitor.facebook.sort_results import SORT_CONFIRM_INTERVAL_MS
from facebook_monitor.facebook.sort_results import SORT_CONFIRM_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_METHOD_NATIVE_LOCATOR
from facebook_monitor.facebook.sort_results import SORT_MUTATION_SUPPRESSION_MS
from facebook_monitor.facebook.sort_results import SORT_MUTATION_SUPPRESSION_REASON
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CLICK_CONTROL
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CONFIRM_LABEL
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CURRENT_LABEL
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_FIND_OPTION
from facebook_monitor.facebook.sort_results import SORT_OPTION_WAIT_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_REASON_ALREADY_PREFERRED_SORT
from facebook_monitor.facebook.sort_results import SORT_REASON_SORT_UPDATE_UNCONFIRMED
from facebook_monitor.facebook.sort_results import SORT_REASON_UPDATED_TO_PREFERRED_SORT
from facebook_monitor.facebook.sort_results import SortAdjustResult
from facebook_monitor.facebook.sort_scripts import COMMENT_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_scripts import FEED_SORT_CURRENT_LABEL_SCRIPT


_FEED_NATIVE_SORT_SPEC = NativeSortSpec(
    target_kind="posts",
    preferred_label=FEED_SORT_NEWEST_LABEL,
    labels=FEED_SORT_LABELS,
    current_label_script=FEED_SORT_CURRENT_LABEL_SCRIPT,
)
_COMMENT_NATIVE_SORT_SPEC = NativeSortSpec(
    target_kind="comments",
    preferred_label=COMMENT_SORT_NEWEST_LABEL,
    labels=COMMENT_SORT_LABELS,
    current_label_script=COMMENT_SORT_CURRENT_LABEL_SCRIPT,
)


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

    return _try_native_sort_click(page, _FEED_NATIVE_SORT_SPEC)

def try_native_comment_sort_click(page: Any) -> NativeSortAttempt:
    """優先用 Playwright trusted click 切 comments sort，失敗時交回 JS fallback。"""

    return _try_native_sort_click(page, _COMMENT_NATIVE_SORT_SPEC)

async def try_native_feed_sort_click_async(page: Any) -> NativeSortAttempt:
    """async resident main 使用的 posts sort native click path。"""

    return await _try_native_sort_click_async(page, _FEED_NATIVE_SORT_SPEC)

async def try_native_comment_sort_click_async(page: Any) -> NativeSortAttempt:
    """async resident main 使用的 comments sort native click path。"""

    return await _try_native_sort_click_async(page, _COMMENT_NATIVE_SORT_SPEC)

def _try_native_sort_click(page: Any, spec: NativeSortSpec) -> NativeSortAttempt:
    """使用 Playwright locator/trusted click 嘗試完成排序調整。"""

    if not page_supports_native_sort_click(page):
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
        diagnostics.update(sort_menu_snapshot_diagnostics(page, spec.preferred_label))
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

    if not page_supports_native_sort_click(page):
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
            await sort_menu_snapshot_diagnostics_async(page, spec.preferred_label)
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

    return click_first_locator(
        sort_control_locators(page, before_label),
        stage=SORT_NATIVE_STAGE_CLICK_CONTROL,
    )

async def _click_sort_control_async(page: Any, before_label: str) -> dict[str, Any]:
    """async 版本：用 role/text locator 點開排序控制。"""

    return await click_first_locator_async(
        sort_control_locators(page, before_label),
        stage=SORT_NATIVE_STAGE_CLICK_CONTROL,
    )

def _click_preferred_sort_option(page: Any, preferred_label: str) -> dict[str, Any]:
    """在 menu scope 內優先點擊 preferred sort option。"""

    diagnostics = menu_root_diagnostics(page, preferred_label)
    option_info = click_first_locator(
        sort_option_locators(page, preferred_label),
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

    diagnostics = await menu_root_diagnostics_async(page, preferred_label)
    option_info = await click_first_locator_async(
        sort_option_locators(page, preferred_label),
        stage=SORT_NATIVE_STAGE_FIND_OPTION,
        wait_timeout_ms=SORT_OPTION_WAIT_TIMEOUT_MS,
    )
    diagnostics.update(option_info)
    diagnostics["clicked_option_text"] = preferred_label
    if not diagnostics.get("menu_opened"):
        diagnostics["menu_opened"] = bool(option_info.get("preferred_option_count"))
    return diagnostics

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
