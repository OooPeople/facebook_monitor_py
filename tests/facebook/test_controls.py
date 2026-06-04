"""Facebook control helper tests。"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from facebook_monitor.facebook.scroll_controls import SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_controls import COMMENT_SCROLL_LOAD_MORE_SCRIPT
from facebook_monitor.facebook.scroll_controls import BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT
from facebook_monitor.facebook.scroll_controls import RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT
from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.comment_mutations import COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_controls import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_CURRENT_LABEL_SCRIPT
from facebook_monitor.facebook.sort_controls import FEED_SORT_LABELS
from facebook_monitor.facebook.sort_controls import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_controls import FEED_SORT_ADJUST_SCRIPT
from facebook_monitor.facebook.sort_controls import SORT_MENU_CANDIDATE_TEXTS_SCRIPT
from facebook_monitor.facebook.sort_controls import ensure_preferred_comment_sort_async
from facebook_monitor.facebook.sort_controls import ensure_preferred_comment_sort
from facebook_monitor.facebook.sort_controls import ensure_preferred_feed_sort
from facebook_monitor.facebook.sort_controls import ensure_preferred_feed_sort_async
from facebook_monitor.facebook.sort_controls import normalize_sort_adjust_result


SUPPORTED_FAKE_SORT_OPTION_ROLES = (
    "menuitemradio",
    "menuitemcheckbox",
    "menuitem",
    "option",
    "radio",
    "checkbox",
    "button",
)


class FakeKeyboard:
    """記錄 sync Playwright keyboard 操作。"""

    def __init__(self) -> None:
        self.presses: list[str] = []

    def press(self, key: str) -> None:
        self.presses.append(key)


class AsyncFakeKeyboard:
    """記錄 async Playwright keyboard 操作。"""

    def __init__(self) -> None:
        self.presses: list[str] = []

    async def press(self, key: str) -> None:
        self.presses.append(key)


class FakeNativeSortPage:
    """模擬 Playwright locator/trusted click 排序控制。"""

    def __init__(
        self,
        *,
        current_label: str = "最相關",
        preferred_label: str = "由新到舊",
        current_label_script: str = COMMENT_SORT_CURRENT_LABEL_SCRIPT,
        fallback_payload: dict[str, object] | None = None,
        control_click_raises: bool = False,
        current_label_evaluate_raises: bool = False,
        confirm_evaluate_raises: bool = False,
        supported_option_roles: tuple[str, ...] = SUPPORTED_FAKE_SORT_OPTION_ROLES,
        menu_root_visible: bool = True,
        candidate_texts: tuple[str, ...] = (),
    ) -> None:
        self.current_label = current_label
        self.preferred_label = preferred_label
        self.current_label_script = current_label_script
        self.fallback_payload = fallback_payload
        self.control_click_raises = control_click_raises
        self.current_label_evaluate_raises = current_label_evaluate_raises
        self.confirm_evaluate_raises = confirm_evaluate_raises
        self.supported_option_roles = supported_option_roles
        self.menu_root_visible = menu_root_visible
        self.candidate_texts = candidate_texts
        self.menu_opened = False
        self.fallback_evaluated = False
        self.role_clicks: list[tuple[str, str]] = []
        self.locator_clicks: list[str] = []
        self.keyboard = FakeKeyboard()

    def evaluate(self, script: str, _preferred_label: str | None = None) -> object:
        if script == self.current_label_script:
            if self.current_label_evaluate_raises:
                raise RuntimeError("current label failed")
            if self.confirm_evaluate_raises and self.locator_clicks:
                raise RuntimeError("confirm failed")
            return self.current_label
        if script == SORT_MENU_CANDIDATE_TEXTS_SCRIPT:
            if self.candidate_texts:
                return list(self.candidate_texts)
            return [self.preferred_label] if self.menu_opened else []
        self.fallback_evaluated = True
        if self.fallback_payload is not None:
            return self.fallback_payload
        return _sort_fallback_payload(
            preferred_label=self.preferred_label,
            before_label="最相關",
            after_label="最相關",
            reason="preferred_sort_option_not_found",
        )

    def get_by_role(self, role: str, *, name: object) -> "FakeNativeSortLocator":
        return FakeNativeSortLocator(self, role=role, name=name, source="page")

    def locator(self, selector: str) -> "FakeNativeSortLocator":
        return FakeNativeSortLocator(self, selector=selector, source="locator")

    def wait_for_timeout(self, _ms: int) -> None:
        return None


class FakeNativeSortLocator:
    """提供 Playwright locator/filter/get_by_role/first/click 的最小替身。"""

    def __init__(
        self,
        page: FakeNativeSortPage,
        *,
        role: str = "",
        name: object = None,
        selector: str = "",
        has_text: str = "",
        source: str = "",
    ) -> None:
        self._page = page
        self._role = role
        self._name = name
        self._selector = selector
        self._has_text = has_text
        self._source = source

    @property
    def first(self) -> "FakeNativeSortLocator":
        return self

    def filter(self, *, has_text: str) -> "FakeNativeSortLocator":
        return FakeNativeSortLocator(
            self._page,
            role=self._role,
            name=self._name,
            selector=self._selector,
            has_text=has_text,
            source=self._source,
        )

    def get_by_role(self, role: str, *, name: object) -> "FakeNativeSortLocator":
        return FakeNativeSortLocator(
            self._page,
            role=role,
            name=name,
            selector=self._selector,
            has_text=self._has_text,
            source="scoped",
        )

    def count(self) -> int:
        return 1 if self._matches() else 0

    def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "visible"
        assert timeout in (1800, 3000)
        if not self._matches():
            raise RuntimeError("locator not visible")

    def click(self, *, timeout: int) -> None:
        assert timeout == 3000
        if not self._matches():
            raise RuntimeError("locator not found")
        if self._is_control():
            self._page.role_clicks.append(("button", self._page.current_label))
            if self._page.control_click_raises:
                raise RuntimeError("control click failed")
            self._page.menu_opened = True
            return
        self._page.locator_clicks.append(self._page.preferred_label)
        self._page.current_label = self._page.preferred_label

    def _matches(self) -> bool:
        if self._is_control():
            return True
        if self._is_menu_root():
            return self._page.menu_opened and self._has_text == self._page.preferred_label
        if self._is_option():
            return self._page.menu_opened and _name_matches(
                self._name,
                self._page.preferred_label,
            )
        return False

    def _is_control(self) -> bool:
        if self._role == "button" and _name_matches(self._name, self._page.current_label):
            return True
        return (
            "[aria-haspopup=\"menu\"]" in self._selector
            and self._has_text == self._page.current_label
        )

    def _is_menu_root(self) -> bool:
        return (
            "[role=\"menu\"]" in self._selector
            and not self._role
            and self._page.menu_root_visible
        )

    def _is_option(self) -> bool:
        return self._role in self._page.supported_option_roles


class AsyncFakeNativeSortPage:
    """async Playwright page 替身。"""

    def __init__(
        self,
        *,
        current_label: str = "最相關",
        preferred_label: str = "由新到舊",
        current_label_script: str = COMMENT_SORT_CURRENT_LABEL_SCRIPT,
        fallback_payload: dict[str, object] | None = None,
        control_click_raises: bool = False,
        current_label_evaluate_raises: bool = False,
        confirm_evaluate_raises: bool = False,
        supported_option_roles: tuple[str, ...] = SUPPORTED_FAKE_SORT_OPTION_ROLES,
        menu_root_visible: bool = True,
        candidate_texts: tuple[str, ...] = (),
    ) -> None:
        self.current_label = current_label
        self.preferred_label = preferred_label
        self.current_label_script = current_label_script
        self.fallback_payload = fallback_payload
        self.control_click_raises = control_click_raises
        self.current_label_evaluate_raises = current_label_evaluate_raises
        self.confirm_evaluate_raises = confirm_evaluate_raises
        self.supported_option_roles = supported_option_roles
        self.menu_root_visible = menu_root_visible
        self.candidate_texts = candidate_texts
        self.menu_opened = False
        self.fallback_evaluated = False
        self.role_clicks: list[tuple[str, str]] = []
        self.locator_clicks: list[str] = []
        self.keyboard = AsyncFakeKeyboard()

    async def evaluate(self, script: str, _preferred_label: str | None = None) -> object:
        if script == self.current_label_script:
            if self.current_label_evaluate_raises:
                raise RuntimeError("current label failed")
            if self.confirm_evaluate_raises and self.locator_clicks:
                raise RuntimeError("confirm failed")
            return self.current_label
        if script == SORT_MENU_CANDIDATE_TEXTS_SCRIPT:
            if self.candidate_texts:
                return list(self.candidate_texts)
            return [self.preferred_label] if self.menu_opened else []
        self.fallback_evaluated = True
        if self.fallback_payload is not None:
            return self.fallback_payload
        return _sort_fallback_payload(
            preferred_label=self.preferred_label,
            before_label="最相關",
            after_label="最相關",
            reason="preferred_sort_option_not_found",
        )

    def get_by_role(self, role: str, *, name: object) -> "AsyncFakeNativeSortLocator":
        return AsyncFakeNativeSortLocator(self, role=role, name=name, source="page")

    def locator(self, selector: str) -> "AsyncFakeNativeSortLocator":
        return AsyncFakeNativeSortLocator(self, selector=selector, source="locator")

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


class AsyncFakeNativeSortLocator:
    """async locator 替身。"""

    def __init__(
        self,
        page: AsyncFakeNativeSortPage,
        *,
        role: str = "",
        name: object = None,
        selector: str = "",
        has_text: str = "",
        source: str = "",
    ) -> None:
        self._page = page
        self._role = role
        self._name = name
        self._selector = selector
        self._has_text = has_text
        self._source = source

    @property
    def first(self) -> "AsyncFakeNativeSortLocator":
        return self

    def filter(self, *, has_text: str) -> "AsyncFakeNativeSortLocator":
        return AsyncFakeNativeSortLocator(
            self._page,
            role=self._role,
            name=self._name,
            selector=self._selector,
            has_text=has_text,
            source=self._source,
        )

    def get_by_role(self, role: str, *, name: object) -> "AsyncFakeNativeSortLocator":
        return AsyncFakeNativeSortLocator(
            self._page,
            role=role,
            name=name,
            selector=self._selector,
            has_text=self._has_text,
            source="scoped",
        )

    async def count(self) -> int:
        return 1 if self._matches() else 0

    async def wait_for(self, *, state: str, timeout: int) -> None:
        assert state == "visible"
        assert timeout in (1800, 3000)
        if not self._matches():
            raise RuntimeError("locator not visible")

    async def click(self, *, timeout: int) -> None:
        assert timeout == 3000
        if not self._matches():
            raise RuntimeError("locator not found")
        if self._is_control():
            self._page.role_clicks.append(("button", self._page.current_label))
            if self._page.control_click_raises:
                raise RuntimeError("control click failed")
            self._page.menu_opened = True
            return
        self._page.locator_clicks.append(self._page.preferred_label)
        self._page.current_label = self._page.preferred_label

    def _matches(self) -> bool:
        if self._is_control():
            return True
        if self._is_menu_root():
            return self._page.menu_opened and self._has_text == self._page.preferred_label
        if self._is_option():
            return self._page.menu_opened and _name_matches(
                self._name,
                self._page.preferred_label,
            )
        return False

    def _is_control(self) -> bool:
        if self._role == "button" and _name_matches(self._name, self._page.current_label):
            return True
        return (
            "[aria-haspopup=\"menu\"]" in self._selector
            and self._has_text == self._page.current_label
        )

    def _is_menu_root(self) -> bool:
        return (
            "[role=\"menu\"]" in self._selector
            and not self._role
            and self._page.menu_root_visible
        )

    def _is_option(self) -> bool:
        return self._role in self._page.supported_option_roles


def _name_matches(name: object, label: str) -> bool:
    """判斷 fake locator 的 name/regex 是否符合 label。"""

    if hasattr(name, "search"):
        return bool(name.search(label))
    return str(name) == label


def _sort_fallback_payload(
    *,
    preferred_label: str,
    before_label: str,
    after_label: str,
    reason: str,
) -> dict[str, object]:
    """建立 JS fallback sort payload。"""

    return {
        "attempted": True,
        "changed": after_label == preferred_label and before_label != after_label,
        "preferredLabel": preferred_label,
        "beforeLabel": before_label,
        "afterLabel": after_label,
        "reason": reason,
        "mutationSuppressionMs": 3200,
        "mutationSuppressionReason": "auto_adjust_sort",
        "menuCandidateTexts": [before_label],
    }


def _run_comment_mutation_direct_signal_cases(values: list[str]) -> list[bool]:
    """用 Node 執行 comments mutation helper，驗證共用清理後的實際判斷。"""

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is required for comment mutation behavior tests")
    script = f"""
globalThis.window = {{}};
class FakeElement {{
    constructor(text) {{
        this.innerText = text;
        this.textContent = text;
        this.parentElement = null;
    }}
    closest(selector) {{
        return null;
    }}
    matches(selector) {{
        if (selector.includes("a[href")) return false;
        return selector.includes('div[dir="auto"]') || selector.includes('span[dir="auto"]');
    }}
}}
globalThis.HTMLElement = FakeElement;
globalThis.HTMLAnchorElement = FakeElement;
const helpers = ({COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT})();
const values = {json.dumps(values, ensure_ascii=False)};
const results = values.map((text) => (
    helpers.mutationTargetHasDirectCommentSignal({{ target: new FakeElement(text) }})
));
console.log(JSON.stringify(results));
"""
    result = subprocess.run(
        [node_bin, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    return json.loads(result.stdout)


def test_feed_sort_labels_match_product_labels() -> None:
    """feed sort 常數需維持產品 label。"""

    assert FEED_SORT_NEWEST_LABEL == "新貼文"
    assert FEED_SORT_LABELS == ("新貼文", "最相關", "最新動態")
    assert "熱門貼文" not in FEED_SORT_ADJUST_SCRIPT
    assert "最近動態" not in FEED_SORT_ADJUST_SCRIPT
    assert "最相關" in FEED_SORT_ADJUST_SCRIPT
    assert "最新動態" in FEED_SORT_ADJUST_SCRIPT


def test_sort_script_uses_exact_feed_option_chain() -> None:
    """排序選項判斷需保留 JS 的 scan target 與 known label chain。"""

    assert "isSortMenuOptionForLabel" in FEED_SORT_ADJUST_SCRIPT
    assert "optionLabels.includes(text)" in FEED_SORT_ADJUST_SCRIPT
    assert "const getCurrentScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "getPreferredSortLabelForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "getCurrentSortControlForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "findPreferredSortMenuOptionForScanTarget" in FEED_SORT_ADJUST_SCRIPT
    assert "unsupported_scan_target" in FEED_SORT_ADJUST_SCRIPT
    assert 'suppressMutationsForMs(3200, "auto_adjust_sort")' in FEED_SORT_ADJUST_SCRIPT
    assert "getSelectorElementsByOrder" in FEED_SORT_ADJUST_SCRIPT


def test_comment_sort_labels_and_script_match_product_semantics() -> None:
    """comments sort 必須使用由新到舊與說明文字判斷鏈。"""

    assert COMMENT_SORT_NEWEST_LABEL == "由新到舊"
    assert COMMENT_SORT_LABELS == ("由新到舊", "最相關", "所有留言")
    assert "isLikelyCommentSortOptionText" in COMMENT_SORT_ADJUST_SCRIPT
    assert "getCurrentCommentSortControl" in COMMENT_SORT_ADJUST_SCRIPT
    assert "findCommentSortMenuOption" in COMMENT_SORT_ADJUST_SCRIPT
    assert "最新的留言顯示在最上方" in COMMENT_SORT_ADJUST_SCRIPT
    assert 'suppressMutationsForMs(3200, "auto_adjust_sort")' in COMMENT_SORT_ADJUST_SCRIPT


def test_comment_sort_script_waits_and_keeps_failure_candidates() -> None:
    """comments sort 失敗時需留下選單候選文字，並避免固定短等待。"""

    assert "waitForPreferredSortOptionForScanTarget" in COMMENT_SORT_ADJUST_SCRIPT
    assert "timeoutMs = 1800" in COMMENT_SORT_ADJUST_SCRIPT
    assert "intervalMs = 120" in COMMENT_SORT_ADJUST_SCRIPT
    assert "await sleep(360)" not in COMMENT_SORT_ADJUST_SCRIPT
    assert "collectVisibleSortCandidateTexts" in COMMENT_SORT_ADJUST_SCRIPT
    assert "menuCandidateTexts: collectVisibleSortCandidateTexts()" in COMMENT_SORT_ADJUST_SCRIPT
    assert 'role === "menuitem"' in COMMENT_SORT_ADJUST_SCRIPT
    assert 'role === "option"' in COMMENT_SORT_ADJUST_SCRIPT
    assert "if (isMenuLike) return true;" in COMMENT_SORT_ADJUST_SCRIPT


def test_native_sort_menu_root_selector_excludes_dialog_roots() -> None:
    """native recovery 不可把 Facebook comments dialog 當成可 Escape 的排序 menu。"""

    from facebook_monitor.facebook.sort_controls import SORT_MENU_ROOT_SELECTOR

    assert '[role="menu"]' in SORT_MENU_ROOT_SELECTOR
    assert '[role="listbox"]' in SORT_MENU_ROOT_SELECTOR
    assert '[role="dialog"]' not in SORT_MENU_ROOT_SELECTOR
    assert "[aria-modal" not in SORT_MENU_ROOT_SELECTOR


def test_js_sort_fallback_menu_root_selector_excludes_dialog_roots() -> None:
    """JS fallback diagnostics 也不可把 Facebook comments dialog 當成 sort menu。"""

    from facebook_monitor.facebook.sort_controls import SORT_MENU_ROOT_SELECTOR

    expected_call = f"document.querySelectorAll({json.dumps(SORT_MENU_ROOT_SELECTOR)})"
    for script in (FEED_SORT_ADJUST_SCRIPT, COMMENT_SORT_ADJUST_SCRIPT):
        assert "__SORT_MENU_ROOT_SELECTOR__" not in script
        assert expected_call in script
        assert '[role="dialog"]' not in script
        assert "[aria-modal" not in script


def test_comment_sort_uses_native_click_before_js_fallback() -> None:
    """comments path 優先用 Playwright trusted click，避免 JS click 打不開 menu。"""

    page = FakeNativeSortPage()

    result = ensure_preferred_comment_sort(page, enabled=True)

    assert page.role_clicks == [("button", "最相關")]
    assert page.locator_clicks == ["由新到舊"]
    assert not page.fallback_evaluated
    assert result.changed
    assert result.before_label == "最相關"
    assert result.after_label == "由新到舊"
    assert result.reason == "updated_to_preferred_sort"
    assert result.to_metadata()["method"] == "native_locator"
    assert result.to_metadata()["menu_opened"] is True
    assert result.to_metadata()["preferred_option_count"] == 1


def test_feed_sort_uses_native_click_before_js_fallback() -> None:
    """posts path 也優先走 trusted click，不只依賴 JS synthetic click。"""

    page = FakeNativeSortPage(
        preferred_label="新貼文",
        current_label_script=FEED_SORT_CURRENT_LABEL_SCRIPT,
        supported_option_roles=("menuitemradio",),
    )

    result = ensure_preferred_feed_sort(page, enabled=True)

    assert page.role_clicks == [("button", "最相關")]
    assert page.locator_clicks == ["新貼文"]
    assert not page.fallback_evaluated
    assert result.changed
    assert result.after_label == "新貼文"
    assert result.to_metadata()["target_kind"] == "posts"
    assert result.to_metadata()["method"] == "native_locator"
    assert result.to_metadata()["option_locator"] == "scoped_menuitemradio"


def test_native_find_option_failure_closes_residual_menu_before_fallback() -> None:
    """已開排序選單但找不到 option 時，fallback 前才按 Escape 關殘留 menu。"""

    page = FakeNativeSortPage(
        fallback_payload=_sort_fallback_payload(
            preferred_label="由新到舊",
            before_label="最相關",
            after_label="最相關",
            reason="preferred_sort_option_not_found",
        ),
        supported_option_roles=(),
    )

    result = ensure_preferred_comment_sort(page, enabled=True)
    metadata = result.to_metadata()

    assert page.fallback_evaluated
    assert page.keyboard.presses == ["Escape"]
    assert metadata["method"] == "js_fallback"
    assert metadata["native_failure_stage"] == "find_option"
    assert metadata["fallback_recovery"] == "escape"
    assert metadata["menu_opened"] is True


def test_native_current_label_failure_does_not_escape_before_fallback() -> None:
    """未碰到排序 menu 時不按 Escape，避免 comments dialog 被關掉。"""

    page = FakeNativeSortPage(
        current_label_evaluate_raises=True,
        fallback_payload=_sort_fallback_payload(
            preferred_label="由新到舊",
            before_label="",
            after_label="",
            reason="sort_control_not_found",
        ),
    )

    result = ensure_preferred_comment_sort(page, enabled=True)
    metadata = result.to_metadata()

    assert page.fallback_evaluated
    assert page.keyboard.presses == []
    assert metadata["method"] == "js_fallback"
    assert metadata["native_failure_stage"] == "current_label"
    assert "fallback_recovery" not in metadata


def test_native_find_option_failure_without_menu_root_does_not_escape() -> None:
    """只有全頁 dialog 文字不是排序選單證據，不可按 Escape。"""

    page = FakeNativeSortPage(
        fallback_payload=_sort_fallback_payload(
            preferred_label="由新到舊",
            before_label="最相關",
            after_label="最相關",
            reason="preferred_sort_option_not_found",
        ),
        supported_option_roles=(),
        menu_root_visible=False,
        candidate_texts=("留言對話框內容", "由新到舊"),
    )

    result = ensure_preferred_comment_sort(page, enabled=True)
    metadata = result.to_metadata()

    assert page.fallback_evaluated
    assert page.keyboard.presses == []
    assert metadata["method"] == "js_fallback"
    assert metadata["native_failure_stage"] == "find_option"
    assert metadata["menu_opened"] is False
    assert "fallback_recovery" not in metadata


def test_native_sort_option_roles_cover_facebook_radio_menu() -> None:
    """native sort 需支援 Facebook posts menuitemradio 選項 markup。"""

    assert '"menuitemradio"' in SORT_MENU_CANDIDATE_TEXTS_SCRIPT
    assert "menuitemradio" in FEED_SORT_ADJUST_SCRIPT


def test_native_sort_failure_falls_back_with_diagnostics() -> None:
    """native locator 失敗不可穿透 pipeline，需回 JS fallback 並保留階段診斷。"""

    page = FakeNativeSortPage(
        control_click_raises=True,
        fallback_payload=_sort_fallback_payload(
            preferred_label="由新到舊",
            before_label="最相關",
            after_label="最相關",
            reason="preferred_sort_option_not_found",
        ),
    )

    result = ensure_preferred_comment_sort(page, enabled=True)
    metadata = result.to_metadata()

    assert page.fallback_evaluated
    assert result.reason == "preferred_sort_option_not_found"
    assert metadata["method"] == "js_fallback"
    assert metadata["fallback_used"] is True
    assert metadata["native_failure_stage"] == "click_control"
    assert metadata["native_exception_class"] == "RuntimeError"


def test_native_confirm_exception_falls_back_with_diagnostics() -> None:
    """native confirm polling transient error 應交給 JS fallback，不升級成例外。"""

    page = FakeNativeSortPage(
        confirm_evaluate_raises=True,
        fallback_payload=_sort_fallback_payload(
            preferred_label="由新到舊",
            before_label="最相關",
            after_label="最相關",
            reason="sort_update_unconfirmed",
        ),
    )

    result = ensure_preferred_comment_sort(page, enabled=True)
    metadata = result.to_metadata()

    assert page.fallback_evaluated
    assert result.reason == "sort_update_unconfirmed"
    assert metadata["method"] == "js_fallback"
    assert metadata["native_failure_stage"] == "confirm_label"
    assert metadata["native_exception_class"] == "RuntimeError"


def test_feed_native_confirm_exception_falls_back_with_diagnostics() -> None:
    """posts native confirm transient error 也應回 JS fallback。"""

    page = FakeNativeSortPage(
        preferred_label="新貼文",
        current_label_script=FEED_SORT_CURRENT_LABEL_SCRIPT,
        confirm_evaluate_raises=True,
        fallback_payload=_sort_fallback_payload(
            preferred_label="新貼文",
            before_label="最相關",
            after_label="最相關",
            reason="sort_update_unconfirmed",
        ),
    )

    result = ensure_preferred_feed_sort(page, enabled=True)
    metadata = result.to_metadata()

    assert page.fallback_evaluated
    assert result.reason == "sort_update_unconfirmed"
    assert metadata["method"] == "js_fallback"
    assert metadata["target_kind"] == "posts"
    assert metadata["native_failure_stage"] == "confirm_label"


def test_async_native_sort_success() -> None:
    """async resident path 也走同一套 native trusted click 狀態機。"""

    async def run_test() -> None:
        page = AsyncFakeNativeSortPage()

        result = await ensure_preferred_comment_sort_async(page, enabled=True)

        assert page.role_clicks == [("button", "最相關")]
        assert page.locator_clicks == ["由新到舊"]
        assert result.changed
        assert result.to_metadata()["method"] == "native_locator"

    asyncio.run(run_test())


def test_async_native_confirm_exception_falls_back_with_diagnostics() -> None:
    """async native confirm transient error 應回 JS fallback。"""

    async def run_test() -> None:
        page = AsyncFakeNativeSortPage(
            confirm_evaluate_raises=True,
            fallback_payload=_sort_fallback_payload(
                preferred_label="由新到舊",
                before_label="最相關",
                after_label="最相關",
                reason="sort_update_unconfirmed",
            ),
        )

        result = await ensure_preferred_comment_sort_async(page, enabled=True)
        metadata = result.to_metadata()

        assert page.fallback_evaluated
        assert result.reason == "sort_update_unconfirmed"
        assert metadata["method"] == "js_fallback"
        assert metadata["native_failure_stage"] == "confirm_label"

    asyncio.run(run_test())


def test_async_native_find_option_failure_closes_residual_menu_before_fallback() -> None:
    """async path 已確認殘留排序 menu 時，fallback 前會按 Escape。"""

    async def run_test() -> None:
        page = AsyncFakeNativeSortPage(
            fallback_payload=_sort_fallback_payload(
                preferred_label="由新到舊",
                before_label="最相關",
                after_label="最相關",
                reason="preferred_sort_option_not_found",
            ),
            supported_option_roles=(),
        )

        result = await ensure_preferred_comment_sort_async(page, enabled=True)
        metadata = result.to_metadata()

        assert page.fallback_evaluated
        assert page.keyboard.presses == ["Escape"]
        assert metadata["method"] == "js_fallback"
        assert metadata["native_failure_stage"] == "find_option"
        assert metadata["fallback_recovery"] == "escape"
        assert metadata["menu_opened"] is True

    asyncio.run(run_test())


def test_async_native_find_option_failure_without_menu_root_does_not_escape() -> None:
    """async path 不能因 comments dialog 全頁文字而關閉 dialog。"""

    async def run_test() -> None:
        page = AsyncFakeNativeSortPage(
            fallback_payload=_sort_fallback_payload(
                preferred_label="由新到舊",
                before_label="最相關",
                after_label="最相關",
                reason="preferred_sort_option_not_found",
            ),
            supported_option_roles=(),
            menu_root_visible=False,
            candidate_texts=("留言對話框內容", "由新到舊"),
        )

        result = await ensure_preferred_comment_sort_async(page, enabled=True)
        metadata = result.to_metadata()

        assert page.fallback_evaluated
        assert page.keyboard.presses == []
        assert metadata["method"] == "js_fallback"
        assert metadata["native_failure_stage"] == "find_option"
        assert metadata["menu_opened"] is False
        assert "fallback_recovery" not in metadata

    asyncio.run(run_test())


def test_async_feed_native_sort_success() -> None:
    """posts async resident path 需覆蓋 native trusted click。"""

    async def run_test() -> None:
        page = AsyncFakeNativeSortPage(
            preferred_label="新貼文",
            current_label_script=FEED_SORT_CURRENT_LABEL_SCRIPT,
        )

        result = await ensure_preferred_feed_sort_async(page, enabled=True)

        assert page.role_clicks == [("button", "最相關")]
        assert page.locator_clicks == ["新貼文"]
        assert result.changed
        assert result.to_metadata()["target_kind"] == "posts"

    asyncio.run(run_test())


def test_sort_adjust_result_preserves_menu_candidate_texts() -> None:
    """sort diagnostics 需保留 comments menu candidate texts 供下輪判讀。"""

    result = normalize_sort_adjust_result(
        {
            "attempted": True,
            "changed": False,
            "preferredLabel": "由新到舊",
            "beforeLabel": "最相關",
            "afterLabel": "最相關",
            "reason": "preferred_sort_option_not_found",
            "mutationSuppressionMs": 3200,
            "mutationSuppressionReason": "auto_adjust_sort",
            "menuCandidateTexts": ["最相關", "由新到舊 最新留言會顯示在最上方"],
        },
        preferred_label="由新到舊",
    )

    assert result.menu_candidate_texts == ("最相關", "由新到舊 最新留言會顯示在最上方")
    assert result.to_metadata()["menu_candidate_texts"] == [
        "最相關",
        "由新到舊 最新留言會顯示在最上方",
    ]


def test_sort_adjust_result_matches_golden_fixture() -> None:
    """sort result normalization golden fixture 鎖住 diagnostics shape。"""

    fixture_path = Path("tests/fixtures/facebook/sort_adjust_golden.json")
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        result = normalize_sort_adjust_result(
            case["payload"],
            preferred_label=case["preferred_label"],
        )
        metadata = result.to_metadata()
        expected = case["expected"]
        assert result.attempted is expected["attempted"], case["name"]
        assert result.changed is expected["changed"], case["name"]
        assert result.preferred_label == expected["preferred_label"], case["name"]
        assert result.before_label == expected["before_label"], case["name"]
        assert result.after_label == expected["after_label"], case["name"]
        assert result.reason == expected["reason"], case["name"]
        assert metadata["menu_candidate_texts"] == expected["menu_candidate_texts"], case["name"]


def test_scroll_script_uses_target_by_and_restore_snapshot() -> None:
    """auto_load_more 不應退回單純 window.scrollBy，需有 target 與 restore 語義。"""

    assert "getLoadMoreScrollTarget" in SCROLL_LOAD_MORE_SCRIPT
    assert "isScrollableElement" in SCROLL_LOAD_MORE_SCRIPT
    assert "findScrollableAncestor" in SCROLL_LOAD_MORE_SCRIPT
    assert "scrollTargetBy" in SCROLL_LOAD_MORE_SCRIPT
    assert "performConfiguredLoadMore" in SCROLL_LOAD_MORE_SCRIPT
    assert "getLoadMoreMode" in SCROLL_LOAD_MORE_SCRIPT
    assert "movedDistance" in SCROLL_LOAD_MORE_SCRIPT
    assert "window.__facebookMonitorLoadMoreSnapshot" in RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT


def test_comment_scroll_script_uses_nested_targets_and_guard() -> None:
    """comments load-more 必須保留 nested scroll target、scoring 與 guard 語義。"""

    assert "collectCommentScrollTargets" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "scoreCommentScrollElement" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "scrollFirstMovable" not in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "comment_nested_scroll" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "movedDistance" in COMMENT_SCROLL_LOAD_MORE_SCRIPT
    assert "window.__facebookMonitorScanRuntime" in BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT
    assert "isLoadingMoreComments" in BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT


def test_comment_mutation_relevance_helpers_match_product_chain() -> None:
    """comments mutation relevance 需保留 permalink/text/suppression 判斷鏈。"""

    assert "elementHasCommentMutationSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "elementHasCommentTextMutationSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "cleanSharedFacebookText" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "顯示較少" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "mutationTargetHasDirectCommentSignal" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "mutationsHaveRelevantCommentNodes" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "shouldRescanForCommentMutation" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT
    assert "__facebookMonitorMutationSuppression" in COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT


def test_comment_mutation_relevance_ignores_pure_expand_label_but_keeps_text() -> None:
    """comments mutation relevance 不應被純 UI label 觸發，也不可誤殺正常文字。"""

    assert _run_comment_mutation_direct_signal_cases(
        [
            "顯示較少",
            "這是一則有票券關鍵字的留言 顯示較少",
            "顯示更多資訊請看留言",
        ]
    ) == [False, True, True]


def test_feed_dom_returns_collected_meta_shape() -> None:
    """DOM extractor 需回傳候選與過濾統計，供 collected_meta 彙整。"""

    assert "return { items, meta };" in POST_LIKE_ITEMS_SCRIPT
    assert "candidateCount: nodes.length" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredEmptyTextCount" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredNonPostCount" in POST_LIKE_ITEMS_SCRIPT
    assert "filteredFeedSortControlCount" in POST_LIKE_ITEMS_SCRIPT
    assert "postsWithPostIdCount" in POST_LIKE_ITEMS_SCRIPT
