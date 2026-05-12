"""Facebook sort control helpers。

職責：對齊 userscript 的保守排序調整語義，在掃描前嘗試把 group feed
切到偏好的「新貼文」排序，並回傳可保存到 scan metadata 的診斷結果。
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def to_metadata(self) -> dict[str, Any]:
        """轉成 scan metadata 使用的穩定欄位。"""

        return {
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


def build_disabled_sort_adjust_result(preferred_label: str) -> SortAdjustResult:
    """建立 auto_adjust_sort 關閉時的標準診斷結果。"""

    return SortAdjustResult(
        attempted=False,
        changed=False,
        preferred_label=preferred_label,
        reason="auto_adjust_sort_disabled",
    )


def normalize_sort_adjust_result(result: object, *, preferred_label: str) -> SortAdjustResult:
    """將 Playwright evaluate 回傳值整理成穩定 SortAdjustResult。"""

    if not isinstance(result, dict):
        return SortAdjustResult(
            attempted=False,
            changed=False,
            preferred_label=preferred_label,
            reason="sort_adjust_result_invalid",
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
    )


def ensure_preferred_feed_sort(page: Any, *, enabled: bool) -> SortAdjustResult:
    """掃描前保守嘗試把 group feed 切到新貼文排序。"""

    if not enabled:
        return build_disabled_sort_adjust_result(FEED_SORT_NEWEST_LABEL)

    result = page.evaluate(FEED_SORT_ADJUST_SCRIPT, FEED_SORT_NEWEST_LABEL)
    return normalize_sort_adjust_result(result, preferred_label=FEED_SORT_NEWEST_LABEL)


def ensure_preferred_comment_sort(page: Any, *, enabled: bool) -> SortAdjustResult:
    """掃描前保守嘗試把單篇貼文留言切到由新到舊。"""

    if not enabled:
        return build_disabled_sort_adjust_result(COMMENT_SORT_NEWEST_LABEL)

    native_result = try_native_comment_sort_click(page)
    if native_result is not None:
        return native_result

    result = page.evaluate(COMMENT_SORT_ADJUST_SCRIPT, COMMENT_SORT_NEWEST_LABEL)
    return normalize_sort_adjust_result(result, preferred_label=COMMENT_SORT_NEWEST_LABEL)


async def ensure_preferred_feed_sort_async(page: Any, *, enabled: bool) -> SortAdjustResult:
    """resident main worker 掃描前嘗試把 group feed 切到新貼文排序。"""

    if not enabled:
        return build_disabled_sort_adjust_result(FEED_SORT_NEWEST_LABEL)

    result = await page.evaluate(FEED_SORT_ADJUST_SCRIPT, FEED_SORT_NEWEST_LABEL)
    return normalize_sort_adjust_result(result, preferred_label=FEED_SORT_NEWEST_LABEL)


async def ensure_preferred_comment_sort_async(
    page: Any,
    *,
    enabled: bool,
) -> SortAdjustResult:
    """resident main worker 掃描前嘗試把留言切到由新到舊。"""

    if not enabled:
        return build_disabled_sort_adjust_result(COMMENT_SORT_NEWEST_LABEL)

    native_result = await try_native_comment_sort_click_async(page)
    if native_result is not None:
        return native_result

    result = await page.evaluate(COMMENT_SORT_ADJUST_SCRIPT, COMMENT_SORT_NEWEST_LABEL)
    return normalize_sort_adjust_result(result, preferred_label=COMMENT_SORT_NEWEST_LABEL)


def build_native_comment_sort_result(before_label: str, after_label: str) -> SortAdjustResult:
    """把 native click 結果整理成既有 sort diagnostics。"""

    return SortAdjustResult(
        attempted=True,
        changed=after_label == COMMENT_SORT_NEWEST_LABEL
        and before_label != after_label,
        preferred_label=COMMENT_SORT_NEWEST_LABEL,
        before_label=before_label,
        after_label=after_label,
        reason=(
            "updated_to_preferred_sort"
            if after_label == COMMENT_SORT_NEWEST_LABEL
            else "sort_update_unconfirmed"
        ),
        mutation_suppression_ms=3200,
        mutation_suppression_reason="auto_adjust_sort",
    )


def try_native_comment_sort_click(page: Any) -> SortAdjustResult | None:
    """優先用 Playwright trusted click 切 comments sort，失敗時交回 JS fallback。"""

    if not hasattr(page, "get_by_role") or not hasattr(page, "get_by_text"):
        return None
    try:
        before_label = str(page.evaluate(COMMENT_SORT_CURRENT_LABEL_SCRIPT) or "")
        if before_label == COMMENT_SORT_NEWEST_LABEL:
            return SortAdjustResult(
                attempted=False,
                changed=False,
                preferred_label=COMMENT_SORT_NEWEST_LABEL,
                before_label=before_label,
                after_label=before_label,
                reason="already_preferred_sort",
            )
        if before_label not in COMMENT_SORT_LABELS:
            return None
        page.get_by_role("button", name=before_label).first.click(timeout=3000)
        page.wait_for_timeout(120)
        page.get_by_text(COMMENT_SORT_NEWEST_LABEL, exact=False).first.click(timeout=3000)
        page.wait_for_timeout(900)
        after_label = str(page.evaluate(COMMENT_SORT_CURRENT_LABEL_SCRIPT) or "")
    except Exception:
        return None
    return build_native_comment_sort_result(before_label, after_label)


async def try_native_comment_sort_click_async(
    page: Any,
) -> SortAdjustResult | None:
    """async resident main 使用的 comments sort native click path。"""

    if not hasattr(page, "get_by_role") or not hasattr(page, "get_by_text"):
        return None
    try:
        before_label = str(await page.evaluate(COMMENT_SORT_CURRENT_LABEL_SCRIPT) or "")
        if before_label == COMMENT_SORT_NEWEST_LABEL:
            return SortAdjustResult(
                attempted=False,
                changed=False,
                preferred_label=COMMENT_SORT_NEWEST_LABEL,
                before_label=before_label,
                after_label=before_label,
                reason="already_preferred_sort",
            )
        if before_label not in COMMENT_SORT_LABELS:
            return None
        await page.get_by_role("button", name=before_label).first.click(timeout=3000)
        await page.wait_for_timeout(120)
        await page.get_by_text(COMMENT_SORT_NEWEST_LABEL, exact=False).first.click(timeout=3000)
        await page.wait_for_timeout(900)
        after_label = str(await page.evaluate(COMMENT_SORT_CURRENT_LABEL_SCRIPT) or "")
    except Exception:
        return None
    return build_native_comment_sort_result(before_label, after_label)


COMMENT_SORT_CURRENT_LABEL_SCRIPT = """
() => {
  const labels = ["由新到舊", "最相關", "所有留言"];
  const descriptionFragments = [
    "顯示所有留言",
    "最新的留言顯示在最上方",
    "優先顯示朋友的留言",
    "獲得最多互動的留言",
    "可能是垃圾訊息",
  ];
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
"""


FEED_SORT_ADJUST_SCRIPT = """
async (preferredLabel) => {
  const labels = ["新貼文", "最相關", "最新動態"];
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
    return element.closest('[role="menuitem"],[role="option"],[role="button"],[aria-checked],[aria-selected],[tabindex]') || element;
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
  const findFeedSortMenuOption = (label = "新貼文") => findSortMenuOption(label, { labels });
  const getPreferredSortLabelForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "posts" ? (preferredLabel || "新貼文") : "";
  };
  const getCurrentSortControlForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "posts" ? getCurrentFeedSortControl() : { label: "", control: null };
  };
  const findPreferredSortMenuOptionForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return findFeedSortMenuOption(getPreferredSortLabelForScanTarget(scanTarget));
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
      reason: "unsupported_scan_target",
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
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
      reason: "already_preferred_sort",
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
    };
  }
  if (!(before.control instanceof HTMLElement)) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: before.label,
      reason: "sort_control_not_found",
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
    };
  }

  suppressMutationsForMs(3200, "auto_adjust_sort");
  clickFacebookControl(before.control);
  await sleep(360);
  const option = findPreferredSortMenuOptionForScanTarget(scanTarget);
  if (!(option instanceof HTMLElement)) {
    return {
      attempted: true,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: getCurrentScanSortLabel(scanTarget),
      reason: "preferred_sort_option_not_found",
      mutationSuppressionMs: 3200,
      mutationSuppressionReason: "auto_adjust_sort",
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
    reason: afterLabel === preferredSortLabel ? "updated_to_preferred_sort" : "sort_update_unconfirmed",
    mutationSuppressionMs: 3200,
    mutationSuppressionReason: "auto_adjust_sort",
  };
}
"""


COMMENT_SORT_ADJUST_SCRIPT = """
async (preferredLabel) => {
  const labels = ["由新到舊", "最相關", "所有留言"];
  const descriptionFragments = [
    "顯示所有留言",
    "最新的留言顯示在最上方",
    "優先顯示朋友的留言",
    "獲得最多互動的留言",
    "可能是垃圾訊息",
  ];
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
    return element.closest('[role="menuitem"],[role="option"],[role="button"],[aria-checked],[aria-selected],[tabindex]') || element;
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
      role === "menuitem" ||
      role === "option" ||
      element.hasAttribute("aria-checked") ||
      element.hasAttribute("aria-selected") ||
      element.closest('[role="menuitem"],[role="option"],[aria-checked],[aria-selected]');
    if (isMenuLike) return true;
    return isDescriptionText(text);
  };
  const findCommentSortMenuOption = (label = "由新到舊") => {
    const selectors = [
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
  const getPreferredSortLabelForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? (preferredLabel || "由新到舊") : "";
  };
  const getCurrentSortControlForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? getCurrentCommentSortControl() : { label: "", control: null };
  };
  const findPreferredSortMenuOptionForScanTarget = (scanTarget = getCurrentScanTarget()) => {
    return scanTarget?.kind === "comments" ? findCommentSortMenuOption(getPreferredSortLabelForScanTarget(scanTarget)) : null;
  };
  const waitForPreferredSortOptionForScanTarget = async (
    scanTarget = getCurrentScanTarget(),
    timeoutMs = 1800,
    intervalMs = 120,
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
      reason: "unsupported_scan_target",
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
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
      reason: "already_preferred_sort",
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
    };
  }
  if (!(before.control instanceof HTMLElement)) {
    return {
      attempted: false,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: before.label,
      reason: "sort_control_not_found",
      mutationSuppressionMs: 0,
      mutationSuppressionReason: "",
    };
  }

  suppressMutationsForMs(3200, "auto_adjust_sort");
  clickFacebookControl(before.control);
  const option = await waitForPreferredSortOptionForScanTarget(scanTarget);
  if (!(option instanceof HTMLElement)) {
    return {
      attempted: true,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: getCurrentScanSortLabel(scanTarget),
      reason: "preferred_sort_option_not_found",
      mutationSuppressionMs: 3200,
      mutationSuppressionReason: "auto_adjust_sort",
      menuCandidateTexts: collectVisibleSortCandidateTexts(),
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
    reason: afterLabel === preferredSortLabel ? "updated_to_preferred_sort" : "sort_update_unconfirmed",
    mutationSuppressionMs: 3200,
    mutationSuppressionReason: "auto_adjust_sort",
  };
}
"""
