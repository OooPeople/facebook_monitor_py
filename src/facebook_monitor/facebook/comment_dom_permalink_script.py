"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_PERMALINK_SCRIPT = r'''  function isVisibleElement(element) {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  }

  function buildDiagnosticCommentHref(value) {
    const url = normalizeFacebookUrl(value);
    if (!url) return String(value || "").slice(0, 220);
    const diagnosticUrl = new URL(`${url.origin}${url.pathname}`);
    for (const key of ["comment_id", "reply_comment_id", "story_fbid", "multi_permalinks", "set"]) {
      for (const item of url.searchParams.getAll(key)) {
        diagnosticUrl.searchParams.append(key, item);
      }
    }
    return diagnosticUrl.toString();
  }

'''

__all__ = ["COMMENT_DOM_PERMALINK_SCRIPT"]
