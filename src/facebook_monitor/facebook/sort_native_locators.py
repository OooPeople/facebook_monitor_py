"""Playwright native sort locator helpers。

職責：集中排序控制與選項 locator 候選、trusted click 與 locator diagnostics。
"""

from __future__ import annotations

import re
from typing import Any

from facebook_monitor.facebook.sort_results import SORT_MENU_ROOT_SELECTOR
from facebook_monitor.facebook.sort_results import SORT_NATIVE_CLICK_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_NATIVE_STAGE_CLICK_CONTROL
from facebook_monitor.facebook.sort_results import SORT_OPTION_ROLES


def page_supports_native_sort_click(page: Any) -> bool:
    """確認 page 至少有 locator trusted click 需要的 Playwright API。"""

    return all(
        hasattr(page, attr)
        for attr in ("evaluate", "get_by_role", "locator", "wait_for_timeout")
    )

def sort_control_locators(page: Any, before_label: str) -> list[tuple[str, Any]]:
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

def sort_option_locators(page: Any, preferred_label: str) -> list[tuple[str, Any]]:
    """依可靠度排序 option locator，先 scoped 到 menu root 再退到 page。"""

    pattern = re.compile(re.escape(preferred_label))
    locators: list[tuple[str, Any]] = []
    menu_root = page.locator(SORT_MENU_ROOT_SELECTOR).filter(has_text=preferred_label)
    for role in SORT_OPTION_ROLES:
        locators.append((f"scoped_{role}", menu_root.get_by_role(role, name=pattern)))
    for role in SORT_OPTION_ROLES:
        locators.append((f"page_{role}", page.get_by_role(role, name=pattern)))
    return locators

def click_first_locator(
    locators: list[tuple[str, Any]],
    *,
    stage: str,
    wait_timeout_ms: int = SORT_NATIVE_CLICK_TIMEOUT_MS,
) -> dict[str, Any]:
    """點擊第一個可用 locator，回傳候選數與 locator 名稱。"""

    last_error: Exception | None = None
    total_count = 0
    for locator_name, locator in locators:
        count = safe_locator_count(locator)
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

async def click_first_locator_async(
    locators: list[tuple[str, Any]],
    *,
    stage: str,
    wait_timeout_ms: int = SORT_NATIVE_CLICK_TIMEOUT_MS,
) -> dict[str, Any]:
    """async 版本：點擊第一個可用 locator。"""

    last_error: Exception | None = None
    total_count = 0
    for locator_name, locator in locators:
        count = await safe_locator_count_async(locator)
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

def safe_locator_count(locator: Any) -> int:
    """安全取得 locator count；失敗時回傳 0 但不阻斷 click auto-wait。"""

    if not hasattr(locator, "count"):
        return 0
    try:
        return int(locator.count())
    except Exception:
        return 0

async def safe_locator_count_async(locator: Any) -> int:
    """async 版本：安全取得 locator count。"""

    if not hasattr(locator, "count"):
        return 0
    try:
        return int(await locator.count())
    except Exception:
        return 0
