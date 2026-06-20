"""Sort menu diagnostics 與 fallback 前 recovery。

職責：擷取排序 menu 狀態與候選文字，並在明確殘留排序 menu 時才按 Escape，
避免誤關 Facebook comments dialog。
"""

from __future__ import annotations

from typing import Any

from facebook_monitor.facebook.sort_native_locators import safe_locator_count
from facebook_monitor.facebook.sort_native_locators import safe_locator_count_async
from facebook_monitor.facebook.sort_results import SORT_MENU_ROOT_SELECTOR
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_FIND_OPTION
from facebook_monitor.facebook.sort_results import SORT_OPTION_WAIT_INTERVAL_MS
from facebook_monitor.facebook.sort_menu_candidate_scripts import (
    SORT_MENU_CANDIDATE_TEXTS_SCRIPT,
)


def _should_recover_sort_menu_before_fallback(diagnostics: dict[str, Any]) -> bool:
    """判斷 native 失敗後是否可安全關閉殘留排序選單。"""

    if diagnostics.get("native_failure_stage") != SORT_NATIVE_STAGE_FIND_OPTION:
        return False
    return (
        diagnostics.get("menu_opened") is True
        and diagnostics.get("menu_role") == SORT_MENU_ROOT_SELECTOR
    )

def recover_sort_menu_before_js_fallback(
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
    diagnostics["fallback_recovery"] = "escape"
    return True

async def recover_sort_menu_before_js_fallback_async(
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
    diagnostics["fallback_recovery"] = "escape"
    return True

def menu_root_diagnostics(page: Any, preferred_label: str) -> dict[str, Any]:
    """檢查 preferred option 所在 menu root 是否已可見。"""

    try:
        root = page.locator(SORT_MENU_ROOT_SELECTOR).filter(has_text=preferred_label)
        count = safe_locator_count(root)
        if count > 0:
            return {"menu_opened": True, "menu_role": SORT_MENU_ROOT_SELECTOR}
    except Exception:
        pass
    return {"menu_opened": False}

def sort_menu_snapshot_diagnostics(page: Any, preferred_label: str) -> dict[str, Any]:
    """native option 失敗時擷取選單與候選文字。"""

    diagnostics = menu_root_diagnostics(page, preferred_label)
    candidate_texts = _collect_visible_sort_candidate_texts(page)
    if candidate_texts:
        diagnostics["menu_candidate_texts"] = candidate_texts
    return diagnostics

async def menu_root_diagnostics_async(
    page: Any,
    preferred_label: str,
) -> dict[str, Any]:
    """async 版本：檢查 preferred option 所在 menu root 是否已可見。"""

    try:
        root = page.locator(SORT_MENU_ROOT_SELECTOR).filter(has_text=preferred_label)
        count = await safe_locator_count_async(root)
        if count > 0:
            return {"menu_opened": True, "menu_role": SORT_MENU_ROOT_SELECTOR}
    except Exception:
        pass
    return {"menu_opened": False}

async def sort_menu_snapshot_diagnostics_async(
    page: Any,
    preferred_label: str,
) -> dict[str, Any]:
    """async 版本：native option 失敗時擷取選單與候選文字。"""

    diagnostics = await menu_root_diagnostics_async(page, preferred_label)
    candidate_texts = await _collect_visible_sort_candidate_texts_async(page)
    if candidate_texts:
        diagnostics["menu_candidate_texts"] = candidate_texts
    return diagnostics

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
