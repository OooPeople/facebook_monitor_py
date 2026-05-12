"""Facebook comments load-more scroll JavaScript payloads。

職責：保存 comments nested scroll target、snapshot / restore 與單次捲動 payload。
"""

from __future__ import annotations

COMMENT_SCROLL_HELPERS_SCRIPT = r"""
() => {
  const commentPermalinkAnchors = 'a[href*="comment_id="], a[href*="reply_comment_id="]';
  const getWindowScrollY = () => Math.round(
    Number(window.scrollY) ||
    Number(window.pageYOffset) ||
    Number(document.scrollingElement?.scrollTop) ||
    Number(document.documentElement?.scrollTop) ||
    Number(document.body?.scrollTop) ||
    0
  );
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const getSelectorElementsByOrder = (scope, selectors) => {
    const elements = [];
    const root = scope && typeof scope.querySelectorAll === "function" ? scope : document;
    for (const selector of Array.isArray(selectors) ? selectors : []) {
      for (const element of root.querySelectorAll(selector)) {
        if (element instanceof HTMLElement) elements.push(element);
      }
    }
    return elements;
  };
  const isOwnScriptUiElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    return Boolean(element.closest?.("#fb-group-refresh-panel,#fbgr-history-modal,#fbgr-settings-modal,#fbgr-include-help-modal,#fbgr-ntfy-help-modal,#fbgr-discord-help-modal"));
  };
  const isElementInActiveScanWindow = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    const viewportHeight = Math.max(Number(window.innerHeight) || 0, 1);
    return rect.bottom >= -viewportHeight * 0.5 && rect.top <= viewportHeight * 1.8;
  };
  const getDocumentScrollElement = () => {
    const candidates = [document.scrollingElement, document.documentElement, document.body];
    return candidates.find((element) => element instanceof HTMLElement) || null;
  };
  const findScrollableAncestor = (element) => {
    let current = element instanceof HTMLElement ? element.parentElement : null;
    let depth = 0;
    while (current instanceof HTMLElement && depth < 12) {
      if (isViableCommentScrollElement(current)) return current;
      current = current.parentElement;
      depth += 1;
    }
    return null;
  };
  const getCommentScrollElement = () => {
    for (const anchor of getSelectorElementsByOrder(document, [commentPermalinkAnchors])) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      if (isOwnScriptUiElement(anchor)) continue;
      if (!isVisibleElement(anchor)) continue;
      if (!isElementInActiveScanWindow(anchor)) continue;
      const scrollableAncestor = findScrollableAncestor(anchor);
      if (scrollableAncestor) return scrollableAncestor;
    }
    return null;
  };
  const getScrollTargetTop = (target) => {
    if (target instanceof HTMLElement) return Math.round(Number(target.scrollTop) || 0);
    return getWindowScrollY();
  };
  const getScrollTargetDebugLabel = (target) => {
    if (target === document.scrollingElement) return "document.scrollingElement";
    if (target === document.documentElement) return "document.documentElement";
    if (target === document.body) return "document.body";
    if (!(target instanceof HTMLElement)) return "window";
    const tag = target.tagName ? target.tagName.toLowerCase() : "element";
    const role = target.getAttribute("role");
    const id = target.id ? `#${target.id}` : "";
    return [tag + id, role ? `role=${role}` : ""].filter(Boolean).join(" ");
  };
  const getScrollTargetDebugMetrics = (target) => {
    const top = getScrollTargetTop(target);
    if (target instanceof HTMLElement) {
      const scrollHeight = Math.round(Number(target.scrollHeight) || 0);
      const clientHeight = Math.round(Number(target.clientHeight) || 0);
      return {
        label: getScrollTargetDebugLabel(target),
        top,
        scrollHeight,
        clientHeight,
        maxScrollTop: Math.max(0, scrollHeight - clientHeight),
      };
    }
    const scrollHeight = Math.max(
      Math.round(Number(document.documentElement?.scrollHeight) || 0),
      Math.round(Number(document.body?.scrollHeight) || 0)
    );
    const clientHeight = Math.round(Number(window.innerHeight) || 0);
    return {
      label: getScrollTargetDebugLabel(target),
      top,
      scrollHeight,
      clientHeight,
      maxScrollTop: Math.max(0, scrollHeight - clientHeight),
    };
  };
  const hasPotentialVerticalScroll = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const scrollHeight = Number(element.scrollHeight) || 0;
    const clientHeight = Number(element.clientHeight) || 0;
    return scrollHeight > clientHeight + 24;
  };
  const isViableCommentScrollElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    if (isOwnScriptUiElement(element)) return false;
    if (!hasPotentialVerticalScroll(element)) return false;
    const rect = element.getBoundingClientRect();
    if (rect.height < 160 || element.clientHeight < 160) return false;
    const style = window.getComputedStyle(element);
    const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
    return /auto|scroll|overlay/.test(overflowY);
  };
  const scoreCommentScrollElement = (element) => {
    if (!(element instanceof HTMLElement)) return 0;
    const metrics = getScrollTargetDebugMetrics(element);
    let commentAnchorCount = 0;
    try {
      commentAnchorCount = element.querySelectorAll(commentPermalinkAnchors).length;
    } catch (error) {
      commentAnchorCount = 0;
    }
    return (
      commentAnchorCount * 500 +
      Math.min(1500, metrics.maxScrollTop) +
      Math.min(300, metrics.clientHeight) / 4 +
      (element.id === "scrollview" ? 120 : 0)
    );
  };
  const appendUniqueScrollTarget = (targets, seen, target) => {
    const key = target instanceof HTMLElement ? target : "window";
    if (seen.has(key)) return false;
    seen.add(key);
    targets.push(target);
    return true;
  };
  const collectPageCommentScrollTargets = (limit = 8) => {
    const candidates = [];
    for (const element of getSelectorElementsByOrder(document, ["body *"])) {
      if (!isViableCommentScrollElement(element)) continue;
      candidates.push({ element, score: scoreCommentScrollElement(element) });
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates.slice(0, limit).map((candidate) => candidate.element);
  };
  const collectCommentScrollTargets = () => {
    const targets = [];
    const seen = new Set();
    const commentScrollElement = getCommentScrollElement();
    if (commentScrollElement) appendUniqueScrollTarget(targets, seen, commentScrollElement);
    for (const anchor of getSelectorElementsByOrder(document, [commentPermalinkAnchors])) {
      if (!(anchor instanceof HTMLElement)) continue;
      if (!isVisibleElement(anchor)) continue;
      if (!isElementInActiveScanWindow(anchor)) continue;
      let current = anchor.parentElement;
      let depth = 0;
      while (current instanceof HTMLElement && depth < 12) {
        if (isViableCommentScrollElement(current)) appendUniqueScrollTarget(targets, seen, current);
        current = current.parentElement;
        depth += 1;
      }
    }
    for (const target of collectPageCommentScrollTargets()) appendUniqueScrollTarget(targets, seen, target);
    appendUniqueScrollTarget(targets, seen, document.scrollingElement);
    appendUniqueScrollTarget(targets, seen, document.documentElement);
    appendUniqueScrollTarget(targets, seen, document.body);
    appendUniqueScrollTarget(targets, seen, null);
    return targets.filter((target) => target === null || target instanceof HTMLElement);
  };
  const getScrollStep = () => Math.max(320, Math.floor((Number(window.innerHeight) || 0) * 0.62));
  const scrollTargetBy = (target, deltaY) => {
    const beforeTop = getScrollTargetTop(target);
    if (target instanceof HTMLElement && typeof target.scrollBy === "function") {
      target.scrollBy(0, deltaY);
    } else if (target instanceof HTMLElement) {
      target.scrollTop = beforeTop + deltaY;
    } else {
      window.scrollBy(0, deltaY);
    }
    const afterTop = getScrollTargetTop(target);
    return afterTop > beforeTop;
  };
  const buildScrollTargetAttempt = (target, beforeMetrics, afterMetrics, moved) => ({
    targetLabel: beforeMetrics.label,
    beforeTop: beforeMetrics.top,
    afterTop: afterMetrics.top,
    scrollHeight: beforeMetrics.scrollHeight,
    clientHeight: beforeMetrics.clientHeight,
    maxScrollTop: beforeMetrics.maxScrollTop,
    movedDistance: Math.max(0, afterMetrics.top - beforeMetrics.top),
    moved: Boolean(moved),
  });
  return {
    collectCommentScrollTargets,
    getScrollTargetTop,
    getScrollTargetDebugMetrics,
    getScrollStep,
    scrollTargetBy,
    buildScrollTargetAttempt,
  };
}
"""


CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT = f"""
() => {{
  const helpers = ({COMMENT_SCROLL_HELPERS_SCRIPT})();
  const targets = helpers.collectCommentScrollTargets();
  window.__facebookMonitorCommentScrollSnapshot = {{
    windowY: Math.round(Number(window.scrollY) || 0),
    targetPositions: targets
      .filter((target) => target instanceof HTMLElement)
      .map((target) => ({{ target, top: helpers.getScrollTargetTop(target) }})),
  }};
  return {{
    captured: true,
    targetCount: targets.length,
    targetLabels: targets.map((target) => helpers.getScrollTargetDebugMetrics(target).label),
  }};
}}
"""


RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT = f"""
() => {{
  const helpers = ({COMMENT_SCROLL_HELPERS_SCRIPT})();
  const snapshot = window.__facebookMonitorCommentScrollSnapshot || null;
  if (!snapshot) return {{ restored: false }};
  for (const entry of snapshot.targetPositions || []) {{
    if (entry.target instanceof HTMLElement) {{
      entry.target.scrollTop = entry.top;
    }}
  }}
  window.scrollTo(0, snapshot.windowY || 0);
  window.__facebookMonitorCommentScrollSnapshot = null;
  return {{ restored: true, windowY: Math.round(Number(window.scrollY) || 0) }};
}}
"""


COMMENT_SCROLL_LOAD_MORE_SCRIPT = f"""
async () => {{
  const helpers = ({COMMENT_SCROLL_HELPERS_SCRIPT})();
  const targets = helpers.collectCommentScrollTargets();
  const attempts = [];
  const scrollStep = helpers.getScrollStep();
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (const target of targets) {{
    const beforeMetrics = helpers.getScrollTargetDebugMetrics(target);
    const moved = helpers.scrollTargetBy(target, scrollStep);
    await sleep(160);
    const afterMetrics = helpers.getScrollTargetDebugMetrics(target);
    const actuallyMoved = Boolean(moved || afterMetrics.top > beforeMetrics.top);
    const attempt = helpers.buildScrollTargetAttempt(
      target,
      beforeMetrics,
      afterMetrics,
      actuallyMoved
    );
    attempts.push(attempt);
    if (actuallyMoved) {{
      return {{
        moved: true,
        loadMoreMode: "comment_nested_scroll",
        targetLabel: attempt.targetLabel,
        beforeTop: attempt.beforeTop,
        afterTop: attempt.afterTop,
        movedDistance: attempt.movedDistance,
        scrollStep,
        scrollHeight: attempt.scrollHeight,
        clientHeight: attempt.clientHeight,
        maxScrollTop: attempt.maxScrollTop,
        attempt,
        attempts,
      }};
    }}
  }}
  const fallback = attempts[0] || {{}};
  return {{
    moved: false,
    loadMoreMode: "comment_nested_scroll",
    targetLabel: fallback.targetLabel || "",
    beforeTop: fallback.beforeTop || 0,
    afterTop: fallback.afterTop || 0,
    movedDistance: fallback.movedDistance || 0,
    scrollStep,
    scrollHeight: fallback.scrollHeight || 0,
    clientHeight: fallback.clientHeight || 0,
    maxScrollTop: fallback.maxScrollTop || 0,
    attempt: fallback,
    attempts,
  }};
}}
"""




__all__ = [
    "CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT",
    "COMMENT_SCROLL_HELPERS_SCRIPT",
    "COMMENT_SCROLL_LOAD_MORE_SCRIPT",
    "RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT",
]
