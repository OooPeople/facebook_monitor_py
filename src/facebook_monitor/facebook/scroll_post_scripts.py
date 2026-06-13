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
  const getDocumentScrollElement = () => {
    const candidates = [
      document.scrollingElement,
      document.documentElement,
      document.body,
    ];
    return candidates.find((element) => element instanceof HTMLElement) || null;
  };
  const getLoadMoreScrollTarget = () => getDocumentScrollElement();
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
