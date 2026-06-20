"""Facebook sort menu candidate text JavaScript payload。

職責：保存 sort menu candidate text evaluate script，供 diagnostics 與
fallback recovery 使用；不承擔 current-label 或 adjustment 行為。
"""

from __future__ import annotations


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
