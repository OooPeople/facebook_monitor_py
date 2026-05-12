"""Facebook posts load-more scroll JavaScript payloads。

職責：保存 posts load-more scroll target、snapshot / restore 與單次捲動 payload。
"""

from __future__ import annotations

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


__all__ = [
    "CAPTURE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT",
    "RESTORE_LOAD_MORE_SCROLL_SNAPSHOT_SCRIPT",
    "SCROLL_HELPERS_SCRIPT",
    "SCROLL_LOAD_MORE_SCRIPT",
    "SCROLL_POSITION_SCRIPT",
]
