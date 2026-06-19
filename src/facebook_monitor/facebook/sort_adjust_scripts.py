"""Facebook sort adjustment fallback JavaScript payloads。

職責：保存 auto_adjust_sort feed/comment fallback evaluate script。
本模組只搬移既有 script constants，不改 Facebook DOM selector 語義。
"""

from __future__ import annotations

import json

from facebook_monitor.facebook.sort_results import COMMENT_SORT_DESCRIPTION_FRAGMENTS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import COMMENT_SORT_OPTION_WAIT_INTERVAL_MS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import FEED_SORT_LABELS
from facebook_monitor.facebook.sort_results import FEED_SORT_NEWEST_LABEL
from facebook_monitor.facebook.sort_results import SORT_MENU_ROOT_SELECTOR
from facebook_monitor.facebook.sort_results import SORT_METHOD_JS_FALLBACK
from facebook_monitor.facebook.sort_results import SORT_MUTATION_SUPPRESSION_MS
from facebook_monitor.facebook.sort_results import SORT_MUTATION_SUPPRESSION_REASON
from facebook_monitor.facebook.sort_results import SORT_OPTION_WAIT_INTERVAL_MS
from facebook_monitor.facebook.sort_results import SORT_OPTION_WAIT_TIMEOUT_MS
from facebook_monitor.facebook.sort_results import SORT_REASON_ALREADY_PREFERRED_SORT
from facebook_monitor.facebook.sort_results import SORT_REASON_PREFERRED_SORT_OPTION_NOT_FOUND
from facebook_monitor.facebook.sort_results import SORT_REASON_SORT_CONTROL_NOT_FOUND
from facebook_monitor.facebook.sort_results import SORT_REASON_SORT_UPDATE_UNCONFIRMED
from facebook_monitor.facebook.sort_results import SORT_REASON_UNSUPPORTED_SCAN_TARGET
from facebook_monitor.facebook.sort_results import SORT_REASON_UPDATED_TO_PREFERRED_SORT


def _js_literal(value: object) -> str:
    """將 Python 常數輸出成 JavaScript literal。"""

    return json.dumps(value, ensure_ascii=False)

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
  const getCurrentFeedSortTarget = () => ({
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
    for (const element of document.querySelectorAll(__SORT_MENU_ROOT_SELECTOR__)) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (text.includes(label)) count += 1;
    }
    return count;
  };
  const getPreferredFeedSortLabel = () => preferredLabel || __FEED_SORT_NEWEST_LABEL__;
  const findPreferredFeedSortMenuOption = () => {
    return findFeedSortMenuOption(getPreferredFeedSortLabel());
  };
  const waitForPreferredFeedSortOption = async (
    timeoutMs = __SORT_OPTION_WAIT_TIMEOUT_MS__,
    intervalMs = __SORT_OPTION_WAIT_INTERVAL_MS__,
  ) => {
    const deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0);
    while (Date.now() <= deadline) {
      const option = findPreferredFeedSortMenuOption();
      if (option instanceof HTMLElement) return option;
      await sleep(intervalMs);
    }
    return null;
  };
  const suppressMutationsForMs = (ms, reason = "") => {
    window.__facebookMonitorMutationSuppression = {
      until: Date.now() + Math.max(0, Math.round(Number(ms) || 0)),
      reason: String(reason || ""),
    };
  };

  const feedSortTarget = getCurrentFeedSortTarget();
  const preferredSortLabel = getPreferredFeedSortLabel();
  if (!feedSortTarget.supported) {
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

  const before = getCurrentFeedSortControl();
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
  const option = await waitForPreferredFeedSortOption();
  const menuRootCount = countVisibleMenuRootsForLabel(preferredSortLabel);
  if (!(option instanceof HTMLElement)) {
    return {
      attempted: true,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: getCurrentFeedSortLabel(),
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
  const afterLabel = getCurrentFeedSortLabel();
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
    "__SORT_MENU_ROOT_SELECTOR__",
    _js_literal(SORT_MENU_ROOT_SELECTOR),
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
  const getCurrentCommentSortTarget = () => ({
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
    for (const element of document.querySelectorAll(__SORT_MENU_ROOT_SELECTOR__)) {
      if (!(element instanceof HTMLElement)) continue;
      if (!isVisibleElement(element)) continue;
      const text = normalizeText(element.innerText || element.textContent || "");
      if (text.includes(label)) count += 1;
    }
    return count;
  };
  const getPreferredCommentSortLabel = () => preferredLabel || __COMMENT_SORT_NEWEST_LABEL__;
  const findPreferredCommentSortMenuOption = () => {
    return findCommentSortMenuOption(getPreferredCommentSortLabel());
  };
  const waitForPreferredCommentSortOption = async (
    timeoutMs = __COMMENT_SORT_OPTION_WAIT_TIMEOUT_MS__,
    intervalMs = __COMMENT_SORT_OPTION_WAIT_INTERVAL_MS__,
  ) => {
    const deadline = Date.now() + Math.max(0, Number(timeoutMs) || 0);
    while (Date.now() <= deadline) {
      const option = findPreferredCommentSortMenuOption();
      if (option instanceof HTMLElement) return option;
      await sleep(intervalMs);
    }
    return null;
  };
  const suppressMutationsForMs = (ms, reason = "") => {
    window.__facebookMonitorMutationSuppression = {
      until: Date.now() + Math.max(0, Math.round(Number(ms) || 0)),
      reason: String(reason || ""),
    };
  };

  const commentSortTarget = getCurrentCommentSortTarget();
  const preferredSortLabel = getPreferredCommentSortLabel();
  if (!commentSortTarget.supported) {
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

  const before = getCurrentCommentSortControl();
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
  const option = await waitForPreferredCommentSortOption();
  const menuRootCount = countVisibleMenuRootsForLabel(preferredSortLabel);
  if (!(option instanceof HTMLElement)) {
    return {
      attempted: true,
      changed: false,
      preferredLabel: preferredSortLabel,
      beforeLabel: before.label,
      afterLabel: getCurrentCommentSortLabel(),
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
  const afterLabel = getCurrentCommentSortLabel();
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
    "__SORT_MENU_ROOT_SELECTOR__",
    _js_literal(SORT_MENU_ROOT_SELECTOR),
).replace(
    "__SORT_METHOD_JS_FALLBACK__",
    _js_literal(SORT_METHOD_JS_FALLBACK),
)
