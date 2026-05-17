"""Facebook comments mutation relevance helpers。

職責：保存 comments target 專用 mutation relevance 語義。
Python resident main worker 目前以固定週期 polling 掃描，沒有啟用 DOM MutationObserver；
本模組先提供可測的 DOM-side 判斷腳本，供 D4/後續即時觸發接線時直接使用。
"""

from __future__ import annotations

from typing import Any


def get_comment_mutation_relevance_diagnostics(page: Any) -> dict[str, Any]:
    """讀取目前頁面與 comments mutation relevance 有關的診斷狀態。"""

    result = page.evaluate(COMMENT_MUTATION_RELEVANCE_DIAGNOSTICS_SCRIPT)
    return result if isinstance(result, dict) else {}


async def get_comment_mutation_relevance_diagnostics_async(page: Any) -> dict[str, Any]:
    """async 版本：讀取 comments mutation relevance 診斷狀態。"""

    result = await page.evaluate(COMMENT_MUTATION_RELEVANCE_DIAGNOSTICS_SCRIPT)
    return result if isinstance(result, dict) else {}


COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT = r"""
() => {
  const commentPermalinkAnchors = 'a[href*="comment_id="], a[href*="reply_comment_id="]';
  const commentTextCandidates = ['div[dir="auto"]', 'span[dir="auto"]'];
  const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const isOwnScriptUiElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    return Boolean(element.closest?.("#fb-group-refresh-panel,#fbgr-history-modal,#fbgr-settings-modal,#fbgr-include-help-modal,#fbgr-ntfy-help-modal,#fbgr-discord-help-modal"));
  };
  const getMutationNodeElement = (node) => {
    if (node instanceof HTMLElement) return node;
    return node?.parentElement instanceof HTMLElement ? node.parentElement : null;
  };
  const getSelectorElementsByOrder = (scope, selectors) => {
    const root = scope && typeof scope.querySelectorAll === "function" ? scope : document;
    const elements = [];
    for (const selector of Array.isArray(selectors) ? selectors : []) {
      for (const element of root.querySelectorAll(selector)) {
        if (element instanceof HTMLElement) elements.push(element);
      }
    }
    return elements;
  };
  const isLikelyCommentTextNode = (text, node) => {
    if (!(node instanceof HTMLElement)) return false;
    const normalized = normalizeText(text);
    if (!normalized || normalized.length < 2) return false;
    if (node.closest("a[href]")) return false;
    return !["讚", "回覆", "分享", "Like", "Reply", "Share"].includes(normalized);
  };
  const elementHasCommentMutationSignal = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;
    if (element.matches?.(commentPermalinkAnchors)) return true;
    return element.querySelector?.(commentPermalinkAnchors) instanceof HTMLAnchorElement;
  };
  const elementHasCommentTextMutationSignal = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;
    const candidateNodes = [];
    if (element.matches?.(commentTextCandidates.join(","))) candidateNodes.push(element);
    for (const node of getSelectorElementsByOrder(element, commentTextCandidates)) {
      candidateNodes.push(node);
      if (candidateNodes.length >= 4) break;
    }
    return candidateNodes.some((node) => {
      const text = normalizeText(node.innerText || node.textContent || "");
      return isLikelyCommentTextNode(text, node);
    });
  };
  const mutationTargetHasDirectCommentSignal = (mutation) => {
    const element = getMutationNodeElement(mutation?.target);
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;
    if (element.matches?.(commentPermalinkAnchors)) return true;
    if (!element.matches?.(commentTextCandidates.join(","))) return false;
    const text = normalizeText(element.innerText || element.textContent || "");
    return isLikelyCommentTextNode(text, element);
  };
  const mutationHasRelevantCommentNode = (mutation) => {
    const targetElement = getMutationNodeElement(mutation?.target);
    if (isOwnScriptUiElement(targetElement)) return false;
    if (mutation?.type && mutation.type !== "childList") {
      return mutationTargetHasDirectCommentSignal(mutation);
    }
    for (const node of mutation?.addedNodes || []) {
      const element = getMutationNodeElement(node);
      if (!element) continue;
      if (elementHasCommentMutationSignal(element)) return true;
      if (elementHasCommentTextMutationSignal(element)) return true;
    }
    return false;
  };
  const mutationsHaveRelevantCommentNodes = (mutations) => {
    return Array.from(mutations || []).some(mutationHasRelevantCommentNode);
  };
  const isMutationSuppressed = () => {
    const suppression = window.__facebookMonitorMutationSuppression || {};
    return Date.now() < Number(suppression.until || 0);
  };
  const shouldRescanForCommentMutation = (mutations) => {
    if (isMutationSuppressed()) return false;
    return mutationsHaveRelevantCommentNodes(mutations);
  };
  return {
    commentPermalinkAnchors,
    commentTextCandidates,
    elementHasCommentMutationSignal,
    elementHasCommentTextMutationSignal,
    mutationTargetHasDirectCommentSignal,
    mutationHasRelevantCommentNode,
    mutationsHaveRelevantCommentNodes,
    shouldRescanForCommentMutation,
    isMutationSuppressed,
  };
}
"""


COMMENT_MUTATION_RELEVANCE_DIAGNOSTICS_SCRIPT = f"""
() => {{
  const helpers = ({COMMENT_MUTATION_RELEVANCE_HELPERS_SCRIPT})();
  return {{
    commentAnchorCount: document.querySelectorAll(helpers.commentPermalinkAnchors).length,
    commentTextCandidateCount: helpers.commentTextCandidates
      .map((selector) => document.querySelectorAll(selector).length)
      .reduce((total, count) => total + count, 0),
    mutationSuppressed: helpers.isMutationSuppressed(),
    helperNames: [
      "elementHasCommentMutationSignal",
      "elementHasCommentTextMutationSignal",
      "mutationTargetHasDirectCommentSignal",
      "mutationHasRelevantCommentNode",
      "mutationsHaveRelevantCommentNodes",
      "shouldRescanForCommentMutation",
    ],
  }};
}}
"""
