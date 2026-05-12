"""Facebook comment load-more guard JavaScript payloads。

職責：保存 comments load-more guard 的 page.evaluate payload。
"""

from __future__ import annotations

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




__all__ = [
    "BEGIN_COMMENT_LOAD_MORE_GUARD_SCRIPT",
    "END_COMMENT_LOAD_MORE_GUARD_SCRIPT",
]
