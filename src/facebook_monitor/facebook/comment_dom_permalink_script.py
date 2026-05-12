"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_PERMALINK_SCRIPT = r'''  function normalizeFacebookUrl(value) {
    try {
      const url = new URL(String(value || ""), location.origin);
      if (!/^(www|m)\.facebook\.com$/i.test(url.hostname)) return null;
      return url;
    } catch (error) {
      return null;
    }
  }

  function extractCommentIdFromValue(value) {
    const text = String(value || "");
    const patterns = [
      /[?&](?:comment_id|reply_comment_id)=(\d{8,})/i,
      /\b(?:comment_id|reply_comment_id|feedback_comment_id)["'=:\s]+(\d{8,})/i,
      /"(?:comment_id|reply_comment_id|feedback_comment_id)":"?(\d+)/i,
    ];
    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match) return match[1];
    }
    return "";
  }

  function extractGroupRouteQueryPostId(url) {
    if (!(url instanceof URL)) return "";
    const values = [
      url.searchParams.get("story_fbid"),
      url.searchParams.get("multi_permalinks"),
      url.searchParams.get("set"),
    ];
    for (const value of values) {
      const text = String(value || "");
      const match = text.match(/\bgm\.(\d+)/i) || text.match(/\b(\d{8,})\b/);
      if (match) return match[1];
    }
    return "";
  }

  function extractGroupPostRouteIdFromUrl(url, expectedGroupId = "") {
    if (!(url instanceof URL)) return "";
    const pathname = url.pathname.replace(/\/+$/, "");
    const groupPostMatch = pathname.match(/^\/groups\/([^/?#]+)\/posts?\/(?:pcb\.)?(\d+)$/i);
    if (groupPostMatch) {
      const [, groupId, postId] = groupPostMatch;
      return expectedGroupId && groupId !== expectedGroupId ? "" : postId;
    }
    const groupPermalinkMatch = pathname.match(/^\/groups\/([^/?#]+)\/permalink\/(\d+)$/i);
    if (groupPermalinkMatch) {
      const [, groupId, postId] = groupPermalinkMatch;
      return expectedGroupId && groupId !== expectedGroupId ? "" : postId;
    }
    const groupRouteMatch = pathname.match(/^\/groups\/([^/?#]+)(?:\/.*)?$/i);
    if (groupRouteMatch) {
      const [, groupId] = groupRouteMatch;
      if (expectedGroupId && groupId !== expectedGroupId) return "";
      return extractGroupRouteQueryPostId(url);
    }
    return "";
  }

  function buildCanonicalGroupCommentUrl(groupId, postId, commentId) {
    const normalizedGroupId = String(groupId || "").trim();
    const normalizedPostId = String(postId || "").trim();
    const normalizedCommentId = String(commentId || "").trim();
    if (
      !normalizedGroupId ||
      !/^\d{8,}$/.test(normalizedPostId) ||
      !/^\d{8,}$/.test(normalizedCommentId)
    ) {
      return "";
    }
    return `https://www.facebook.com/groups/${normalizedGroupId}/posts/${normalizedPostId}/?comment_id=${normalizedCommentId}`;
  }

  function isVisibleElement(element) {
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
