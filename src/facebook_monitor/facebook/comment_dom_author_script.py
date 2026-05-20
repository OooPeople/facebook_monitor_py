"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_AUTHOR_SCRIPT = r'''  function isLikelyCommentAuthorHref(value) {
    const url = normalizeFacebookUrl(value);
    if (!url) return false;
    const pathname = url.pathname.replace(/\/+$/, "");
    if (/^\/hashtag\//i.test(pathname)) return false;
    return !extractCommentIdFromValue(url.href);
  }

  function extractCommentAuthor(container, commentAnchor) {
    const candidates = [];
    for (const anchor of container.querySelectorAll('a[role="link"], a[href]')) {
      const href = anchor.href || anchor.getAttribute("href") || "";
      if (!isLikelyCommentAuthorHref(href)) continue;
      const text = normalizeText(anchor.innerText || anchor.textContent || "");
      if (
        !text ||
        text.length > 80 ||
        text.startsWith("#") ||
        nonBodyLabels.has(text) ||
        isFacebookExpandCollapseLabelText(text)
      ) continue;
      const authorRect = anchor.getBoundingClientRect();
      const commentRect = commentAnchor?.getBoundingClientRect?.();
      const distance = commentRect ? Math.abs(authorRect.top - commentRect.top) : 0;
      candidates.push({ text, score: Math.max(0, 1000 - Math.round(distance)) });
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.text || "";
  }

'''

__all__ = ["COMMENT_DOM_AUTHOR_SCRIPT"]
