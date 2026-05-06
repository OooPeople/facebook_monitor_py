"""Facebook comments DOM extractor script。

職責：移植 userscript D2 的可見留言抽取、comment canonical URL 與文字清理語義。
不包含留言排序、滾動載入更多或 mutation relevance。
"""

COMMENTS_LIKE_ITEMS_SCRIPT = r"""
(payload) => {
  const limit = Math.max(Number(payload?.limit || 5), 1);
  const scanTarget = {
    groupId: String(payload?.groupId || ""),
    parentPostId: String(payload?.parentPostId || ""),
  };
  const commentPermalinkAnchors = 'a[href*="comment_id="], a[href*="reply_comment_id="]';
  const commentTextCandidates = 'div[dir="auto"], span[dir="auto"]';
  const commentActionTrail = [
    /(?:^|\s)(讚|回覆|分享|檢舉|隱藏|Like|Reply|Share)(?:\s|$)/gi,
    /\b(?:\d+\s*)?(?:分鐘|小時|天|週|月|年|m|h|d|w|mo|y)\b/gi,
  ];
  const nonBodyLabels = new Set([
    "讚",
    "回覆",
    "分享",
    "編輯紀錄",
    "查看更多",
    "顯示更多",
    "Like",
    "Reply",
    "Share",
    "See more",
  ]);

  function normalizeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function cleanCommentExtractedText(value) {
    let text = normalizeText(value);
    for (const pattern of commentActionTrail) {
      text = text.replace(pattern, " ");
    }
    for (const label of nonBodyLabels) {
      text = text.replaceAll(label, " ");
    }
    return collapseRepeatedAdjacentText(text);
  }

  function collapseRepeatedAdjacentText(value) {
    let text = normalizeText(value);
    if (!text) return "";
    while (true) {
      const tokens = text.split(" ");
      if (tokens.length > 1 && tokens.length % 2 === 0) {
        const halfLength = tokens.length / 2;
        const left = tokens.slice(0, halfLength).join(" ");
        const right = tokens.slice(halfLength).join(" ");
        if (left.length >= 8 && left === right) {
          text = left;
          continue;
        }
      }
      if (text.length % 2 === 0) {
        const halfLength = text.length / 2;
        const left = text.slice(0, halfLength);
        const right = text.slice(halfLength);
        if (left.length >= 8 && left === right) {
          text = left;
          continue;
        }
      }
      return text;
    }
  }

  function normalizeFacebookUrl(value) {
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

  function findCommentContainerFromPermalinkAnchor(anchor) {
    const candidates = [
      anchor.closest('[role="article"]'),
      anchor.closest('div[aria-label]'),
      anchor.closest('li'),
      anchor.parentElement?.parentElement?.parentElement,
      anchor.parentElement?.parentElement,
    ];
    for (const candidate of candidates) {
      if (candidate instanceof HTMLElement && normalizeText(candidate.innerText || candidate.textContent || "")) {
        return candidate;
      }
    }
    return anchor.closest("div");
  }

  function isLikelyCommentTextNode(text, node) {
    if (!(node instanceof HTMLElement)) return false;
    const normalized = normalizeText(text);
    if (!normalized || normalized.length < 2) return false;
    if (nonBodyLabels.has(normalized)) return false;
    if (node.closest("a[href]")) return false;
    return true;
  }

  function extractCommentTextDetails(container) {
    const snippets = [];
    const seen = new Set();
    for (const node of container.querySelectorAll(commentTextCandidates)) {
      const text = cleanCommentExtractedText(node.innerText || node.textContent || "");
      if (!isLikelyCommentTextNode(text, node)) continue;
      if (seen.has(text)) continue;
      seen.add(text);
      snippets.push(text);
      if (snippets.length >= 6) break;
    }
    if (snippets.length) {
      const rawText = normalizeText(snippets.join(" "));
      return { text: cleanCommentExtractedText(rawText), rawText, source: "comment" };
    }
    const rawText = normalizeText(container.innerText || container.textContent || "");
    return { text: cleanCommentExtractedText(rawText), rawText, source: "container" };
  }

  function isLikelyCommentAuthorHref(value) {
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
      if (!text || text.length > 80 || text.startsWith("#") || nonBodyLabels.has(text)) continue;
      const authorRect = anchor.getBoundingClientRect();
      const commentRect = commentAnchor?.getBoundingClientRect?.();
      const distance = commentRect ? Math.abs(authorRect.top - commentRect.top) : 0;
      candidates.push({ text, score: Math.max(0, 1000 - Math.round(distance)) });
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates[0]?.text || "";
  }

  const items = [];
  const seen = new Set();
  let candidateCount = 0;
  let filteredEmptyTextCount = 0;
  let filteredNonPostCount = 0;
  let articleElementCount = 0;
  let commentsWithCommentIdCount = 0;

  for (const anchor of document.querySelectorAll(commentPermalinkAnchors)) {
    if (!(anchor instanceof HTMLAnchorElement)) continue;
    if (!isVisibleElement(anchor)) continue;
    const href = anchor.href || anchor.getAttribute("href") || "";
    const commentId = extractCommentIdFromValue(href);
    if (!commentId || seen.has(commentId)) continue;
    const container = findCommentContainerFromPermalinkAnchor(anchor);
    if (!(container instanceof HTMLElement) || !isVisibleElement(container)) continue;
    seen.add(commentId);
    candidateCount += 1;

    const url = normalizeFacebookUrl(href);
    const routePostId = extractGroupPostRouteIdFromUrl(url, scanTarget.groupId);
    const parentPostId = routePostId || scanTarget.parentPostId;
    const permalink = buildCanonicalGroupCommentUrl(scanTarget.groupId, parentPostId, commentId) || href;
    const textDetails = extractCommentTextDetails(container);
    const text = textDetails.text;
    if (!text) {
      filteredEmptyTextCount += 1;
      continue;
    }
    if (!commentId && !permalink) {
      filteredNonPostCount += 1;
      continue;
    }
    if (container.matches('[role="article"]')) {
      articleElementCount += 1;
    }
    if (commentId) {
      commentsWithCommentIdCount += 1;
    }

    items.push({
      itemKind: "comment",
      commentId,
      parentPostId,
      groupId: scanTarget.groupId,
      permalink,
      permalinkSource: permalink ? "comment_anchor" : "unavailable",
      canonicalPermalinkCandidateCount: permalink ? 1 : 0,
      author: extractCommentAuthor(container, anchor),
      text,
      textLength: text.length,
      rawTextLength: textDetails.rawText.length,
      textSource: textDetails.source,
      linkCount: container.querySelectorAll("a[href]").length,
      source: "comment_permalink_anchor",
      containerRole: container.matches('[role="article"]') ? "article" : "comment_container",
    });
    if (items.length >= limit) break;
  }

  return {
    items,
    meta: {
      mode: "comments_visible_window",
      targetCount: limit,
      candidateCount,
      parsedCount: items.length,
      accumulatedCount: items.length,
      filteredEmptyTextCount,
      filteredNonPostCount,
      articleElementCount,
      commentsWithCommentIdCount,
      stopReason: "visible_window_completed",
    },
  };
}
"""
