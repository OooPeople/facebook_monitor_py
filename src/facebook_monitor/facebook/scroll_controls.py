"""Facebook load-more scroll helpers。

職責：對齊 userscript 的 posts load-more 捲動語義，集中處理 scroll target
選擇、scroll snapshot / restore 與單次捲動診斷。
"""

from __future__ import annotations

from typing import Any


def get_scroll_position(page: Any) -> dict[str, Any]:
    """取得目前文件捲動位置與尺寸，供每輪 scan metadata 使用。"""

    result = page.evaluate(SCROLL_POSITION_SCRIPT)
    return result if isinstance(result, dict) else {}


async def get_scroll_position_async(page: Any) -> dict[str, Any]:
    """resident main worker 取得目前文件捲動位置與尺寸。"""

    result = await page.evaluate(SCROLL_POSITION_SCRIPT)
    return result if isinstance(result, dict) else {}


def capture_load_more_scroll_snapshot(page: Any) -> dict[str, Any]:
    """在深度掃描前保存 scroll 位置，避免干擾使用者視窗。"""

    result = page.evaluate(CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


async def capture_load_more_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """resident main worker 在深度掃描前保存 scroll 位置。"""

    result = await page.evaluate(CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


def restore_load_more_scroll_snapshot(page: Any) -> dict[str, Any]:
    """深度掃描結束後復原 scroll 位置。"""

    result = page.evaluate(RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


async def restore_load_more_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """resident main worker 深度掃描結束後復原 scroll 位置。"""

    result = await page.evaluate(RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


def scroll_load_more(page: Any) -> dict[str, Any]:
    """執行一次 posts load-more 捲動並回傳目標與位移診斷。"""

    result = page.evaluate(SCROLL_LOAD_MORE_SCRIPT)
    return result if isinstance(result, dict) else {}


def begin_comment_load_more_guard(page: Any) -> dict[str, Any]:
    """取得 comment-specific load-more guard，避免同頁留言捲動互相打架。"""

    result = page.evaluate(BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    return result if isinstance(result, dict) else {}


def end_comment_load_more_guard(page: Any) -> dict[str, Any]:
    """釋放 comment-specific load-more guard。"""

    result = page.evaluate(END_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    return result if isinstance(result, dict) else {}


def capture_comment_scroll_snapshot(page: Any) -> dict[str, Any]:
    """保存 comments 可能碰到的 nested scroll targets 位置。"""

    result = page.evaluate(CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


def restore_comment_scroll_snapshot(page: Any) -> dict[str, Any]:
    """復原 comments nested scroll targets 位置。"""

    result = page.evaluate(RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


def scroll_comment_load_more(page: Any) -> dict[str, Any]:
    """對 comments nested scroll candidates 執行一次保守 load-more。"""

    result = page.evaluate(COMMENT_SCROLL_LOAD_MORE_SCRIPT)
    return result if isinstance(result, dict) else {}


async def scroll_load_more_async(page: Any) -> dict[str, Any]:
    """resident main worker 執行一次 posts load-more 捲動。"""

    result = await page.evaluate(SCROLL_LOAD_MORE_SCRIPT)
    return result if isinstance(result, dict) else {}


async def begin_comment_load_more_guard_async(page: Any) -> dict[str, Any]:
    """async 版本：取得 comment-specific load-more guard。"""

    result = await page.evaluate(BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    return result if isinstance(result, dict) else {}


async def end_comment_load_more_guard_async(page: Any) -> dict[str, Any]:
    """async 版本：釋放 comment-specific load-more guard。"""

    result = await page.evaluate(END_COMMENT_LOAD_MORE_GUARD_SCRIPT)
    return result if isinstance(result, dict) else {}


async def capture_comment_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """async 版本：保存 comments nested scroll targets 位置。"""

    result = await page.evaluate(CAPTURE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


async def restore_comment_scroll_snapshot_async(page: Any) -> dict[str, Any]:
    """async 版本：復原 comments nested scroll targets 位置。"""

    result = await page.evaluate(RESTORE_COMMENT_SCROLL_SNAPSHOT_SCRIPT)
    return result if isinstance(result, dict) else {}


async def scroll_comment_load_more_async(page: Any) -> dict[str, Any]:
    """async 版本：對 comments nested scroll candidates 執行一次 load-more。"""

    result = await page.evaluate(COMMENT_SCROLL_LOAD_MORE_SCRIPT)
    return result if isinstance(result, dict) else {}


def _build_scroll_script(body: str) -> str:
    """用同一份 JS helper 組出 Playwright evaluate 可執行的 IIFE。"""

    return (
        "() => {\n"
        f"  const helpers = ({SCROLL_HELPERS_SCRIPT})();\n"
        f"{body}\n"
        "}"
    )


SCROLL_HELPERS_SCRIPT = r"""
() => {
  const getWindowScrollY = () => Math.round(Number(window.scrollY) || 0);
  const isVisibleElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  };
  const isScrollableElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(element);
    const overflowY = String(style.overflowY || style.overflow || "").toLowerCase();
    const allowsScroll = /auto|scroll|overlay/.test(overflowY);
    const scrollHeight = Number(element.scrollHeight) || 0;
    const clientHeight = Number(element.clientHeight) || 0;
    return allowsScroll && scrollHeight > clientHeight + 24;
  };
  const findScrollableAncestor = (element) => {
    let current = element instanceof HTMLElement ? element.parentElement : null;
    let depth = 0;
    while (current instanceof HTMLElement && depth < 12) {
      if (isScrollableElement(current)) return current;
      current = current.parentElement;
      depth += 1;
    }
    return null;
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
  const getCurrentScanTarget = () => ({ kind: "posts" });
  const getDocumentScrollElement = () => {
    const candidates = [
      document.scrollingElement,
      document.documentElement,
      document.body,
    ];
    return candidates.find((element) => element instanceof HTMLElement) || null;
  };
  const getCommentScrollElement = () => {
    for (const anchor of getSelectorElementsByOrder(document, [
      'a[href*="comment_id="]',
      'a[href*="/comments/"]',
    ])) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      if (!isVisibleElement(anchor)) continue;
      const scrollableAncestor = findScrollableAncestor(anchor);
      if (scrollableAncestor) return scrollableAncestor;
    }
    return null;
  };
  const getLoadMoreScrollTarget = () => {
    if (getCurrentScanTarget().kind === "comments") {
      return getCommentScrollElement() || getDocumentScrollElement();
    }
    return getDocumentScrollElement();
  };
  const getScrollTargetTop = (target) => {
    if (target instanceof HTMLElement) {
      return Math.round(Number(target.scrollTop) || 0);
    }
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
    const documentElement = document.documentElement;
    const body = document.body;
    const scrollHeight = Math.max(
      Math.round(Number(documentElement?.scrollHeight) || 0),
      Math.round(Number(body?.scrollHeight) || 0)
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
  const getScrollStep = () => Math.max(Math.round(Number(window.innerHeight) * 1.2), 900);
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
  const getLoadMoreMode = () => "scroll";
  const performScrollLoad = () => scrollTargetBy(getLoadMoreScrollTarget(), getScrollStep());
  const performConfiguredLoadMore = () => performScrollLoad();
  return {
    getWindowScrollY,
    isScrollableElement,
    findScrollableAncestor,
    getLoadMoreScrollTarget,
    getScrollTargetTop,
    getScrollTargetDebugMetrics,
    getScrollStep,
    scrollTargetBy,
    getLoadMoreMode,
    performConfiguredLoadMore,
  };
}
"""


SCROLL_POSITION_SCRIPT = _build_scroll_script(
    r"""
  const target = helpers.getLoadMoreScrollTarget();
  const metrics = helpers.getScrollTargetDebugMetrics(target);
  return {
    scrollY: helpers.getWindowScrollY(),
    scrollHeight: metrics.scrollHeight,
    scrollTargetLabel: metrics.label,
    scrollTargetTop: metrics.top,
    scrollTargetClientHeight: metrics.clientHeight,
    scrollTargetMaxTop: metrics.maxScrollTop,
  };
"""
)


BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT = """
() => {
  const runtime = window.__facebookMonitorScanRuntime || {};
  if (runtime.isLoadingMoreComments) {
    return { acquired: false, reason: "comment_load_more_guard_active" };
  }
  window.__facebookMonitorScanRuntime = {
    ...runtime,
    isLoadingMoreComments: true,
  };
  return { acquired: true, reason: "comment_load_more_guard_acquired" };
}
"""


END_COMMENT_LOAD_MORE_GUARD_SCRIPT = """
() => {
  const runtime = window.__facebookMonitorScanRuntime || {};
  window.__facebookMonitorScanRuntime = {
    ...runtime,
    isLoadingMoreComments: false,
  };
  return { released: true };
}
"""


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


CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT = _build_scroll_script(
    r"""
  const target = helpers.getLoadMoreScrollTarget();
  window.__facebookMonitorLoadMoreSnapshot = {
    target,
    targetTop: helpers.getScrollTargetTop(target),
    windowY: helpers.getWindowScrollY(),
  };
  return {
    captured: true,
    targetLabel: helpers.getScrollTargetDebugMetrics(target).label,
    targetTop: window.__facebookMonitorLoadMoreSnapshot.targetTop,
    windowY: window.__facebookMonitorLoadMoreSnapshot.windowY,
  };
"""
)


RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT = _build_scroll_script(
    r"""
  const snapshot = window.__facebookMonitorLoadMoreSnapshot || null;
  if (!snapshot) return { restored: false };
  if (snapshot.target instanceof HTMLElement) {
    snapshot.target.scrollTop = snapshot.targetTop;
  }
  window.scrollTo(0, snapshot.windowY || 0);
  const target = helpers.getLoadMoreScrollTarget();
  const metrics = helpers.getScrollTargetDebugMetrics(target);
  window.__facebookMonitorLoadMoreSnapshot = null;
  return {
    restored: true,
    targetLabel: metrics.label,
    targetTop: metrics.top,
    windowY: helpers.getWindowScrollY(),
  };
"""
)


SCROLL_LOAD_MORE_SCRIPT = _build_scroll_script(
    r"""
  const target = helpers.getLoadMoreScrollTarget();
  const before = helpers.getScrollTargetDebugMetrics(target);
  const scrollStep = helpers.getScrollStep();
  const moved = helpers.performConfiguredLoadMore();
  const after = helpers.getScrollTargetDebugMetrics(target);
  return {
    moved: Boolean(moved || after.top > before.top),
    loadMoreMode: helpers.getLoadMoreMode(),
    targetLabel: before.label,
    beforeTop: before.top,
    afterTop: after.top,
    movedDistance: Math.max(0, after.top - before.top),
    scrollStep,
    scrollHeight: before.scrollHeight,
    clientHeight: before.clientHeight,
    maxScrollTop: before.maxScrollTop,
  };
"""
)
