"""Facebook sort current-label JavaScript payloads。

職責：保存 auto_adjust_sort 的 feed/comment current-label evaluate script。
本模組只搬移既有 script constants，不改 Facebook DOM selector 語義。
"""

from __future__ import annotations

import json

from facebook_monitor.facebook.sort_results import COMMENT_SORT_DESCRIPTION_FRAGMENTS
from facebook_monitor.facebook.sort_results import COMMENT_SORT_LABELS
from facebook_monitor.facebook.sort_results import FEED_SORT_LABELS


def _js_literal(value: object) -> str:
    """將 Python 常數輸出成 JavaScript literal。"""

    return json.dumps(value, ensure_ascii=False)

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
