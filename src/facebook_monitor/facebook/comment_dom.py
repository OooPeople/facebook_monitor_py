"""Facebook comments DOM extractor script。

職責：移植 userscript D2 的可見留言抽取、comment canonical URL 與文字清理語義。
不包含留言排序、滾動載入更多或 mutation relevance。
"""

from facebook_monitor.facebook.text_snippet_dom import TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT


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

""" + TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT + r"""

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

  function getCurrentRoutePostId() {
    return extractGroupPostRouteIdFromUrl(normalizeFacebookUrl(location.href), scanTarget.groupId);
  }

  function routePostIdMatchesTarget(postId) {
    return Boolean(postId && scanTarget.parentPostId && postId === scanTarget.parentPostId);
  }

  function rootContainsTargetPostLink(root) {
    if (!(root instanceof HTMLElement) && root !== document) return false;
    for (const anchor of root.querySelectorAll?.("a[href]") || []) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      const routePostId = extractGroupPostRouteIdFromUrl(
        normalizeFacebookUrl(anchor.href || anchor.getAttribute("href") || ""),
        scanTarget.groupId
      );
      if (routePostIdMatchesTarget(routePostId)) return true;
    }
    return false;
  }

  function getCommentSearchRootLabel(root) {
    if (root === document) return "document";
    if (!(root instanceof HTMLElement)) return "unknown";
    const tag = String(root.tagName || "element").toLowerCase();
    const role = root.getAttribute("role") || "";
    const ariaModal = root.getAttribute("aria-modal") || "";
    return [tag, role ? `role=${role}` : "", ariaModal ? `aria-modal=${ariaModal}` : ""]
      .filter(Boolean)
      .join(" ");
  }

  function collectCommentSearchRoots() {
    const currentRoutePostId = getCurrentRoutePostId();
    const currentRouteMatchesTarget = routePostIdMatchesTarget(currentRoutePostId);
    const dialogRoots = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
      .filter((root) => root instanceof HTMLElement)
      .filter((root) => isVisibleElement(root))
      .filter((root) => root.querySelector(commentPermalinkAnchors) instanceof HTMLAnchorElement);
    const targetDialogRoots = dialogRoots.filter((root) => rootContainsTargetPostLink(root));
    const roots = targetDialogRoots.length
      ? targetDialogRoots
      : (
          dialogRoots.length && currentRouteMatchesTarget
            ? dialogRoots
            : (dialogRoots.length ? dialogRoots : [document])
        );
    const strategy = targetDialogRoots.length
      ? "target_dialog"
      : (
          dialogRoots.length && currentRouteMatchesTarget
            ? "dialog_current_route"
            : (dialogRoots.length ? "dialog_without_route_match" : "document")
        );
    return {
      roots,
      strategy,
      currentRoutePostId,
      currentRouteMatchesTarget,
    };
  }

  function evaluateCommentTargetScope({ routePostId, root, searchRoots }) {
    const normalizedRoutePostId = String(routePostId || "");
    const targetParentPostId = String(scanTarget.parentPostId || "");
    if (normalizedRoutePostId && targetParentPostId && normalizedRoutePostId === targetParentPostId) {
      return {
        accepted: true,
        reason: "route_post_match",
        routePostIdMatchesTarget: true,
        routePostIdSource: "comment_anchor_href",
      };
    }
    if (normalizedRoutePostId && targetParentPostId && normalizedRoutePostId !== targetParentPostId) {
      return {
        accepted: false,
        reason: "route_post_mismatch",
        routePostIdMatchesTarget: false,
        routePostIdSource: "comment_anchor_href",
      };
    }
    if (rootContainsTargetPostLink(root)) {
      return {
        accepted: true,
        reason: "target_root_fallback",
        routePostIdMatchesTarget: false,
        routePostIdSource: "target_root",
      };
    }
    if (searchRoots.currentRouteMatchesTarget && searchRoots.strategy !== "document") {
      return {
        accepted: true,
        reason: `${searchRoots.strategy}_fallback`,
        routePostIdMatchesTarget: false,
        routePostIdSource: "current_route",
      };
    }
    if (searchRoots.currentRouteMatchesTarget && searchRoots.strategy === "document") {
      return {
        accepted: true,
        reason: "document_current_route_fallback",
        routePostIdMatchesTarget: false,
        routePostIdSource: "current_route",
      };
    }
    return {
      accepted: false,
      reason: "missing_route_post_id_unscoped",
      routePostIdMatchesTarget: false,
      routePostIdSource: "none",
    };
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
    return !getCommentTextNodeRejectionReason(text, node);
  }

  function getCommentTextNodeRejectionReason(text, node) {
    if (!(node instanceof HTMLElement)) return "non_element";
    const normalized = normalizeText(text);
    if (!normalized) return "empty_after_clean";
    if (normalized.length < 2) return "too_short";
    if (nonBodyLabels.has(normalized)) return "non_body_label";
    if (node.closest("a[href]")) return "inside_anchor";
    return "";
  }

  function pushCommentTextDiagnosticSample(diagnostics, sample, limit = 8) {
    const reason = sample.reason || "included";
    diagnostics.reasonCounts[reason] = (diagnostics.reasonCounts[reason] || 0) + 1;
    if (sample.included) {
      diagnostics.includedCount += 1;
    }
    if (diagnostics.samples.length >= limit) return;
    diagnostics.samples.push({
      reason,
      included: Boolean(sample.included),
      text: normalizeText(sample.text).slice(0, 180),
      rawText: normalizeText(sample.rawText).slice(0, 180),
      textLength: normalizeText(sample.text).length,
      rawTextLength: normalizeText(sample.rawText).length,
      tagName: sample.node?.tagName || "",
      role: sample.node?.getAttribute?.("role") || "",
      ariaLabel: normalizeText(sample.node?.getAttribute?.("aria-label") || "").slice(0, 80),
      insideAnchor: Boolean(sample.node?.closest?.("a[href]")),
      isContainedByPreviousSnippet: Boolean(sample.isContainedByPreviousSnippet),
      containsPreviousSnippet: Boolean(sample.containsPreviousSnippet),
      replacedContainedSnippetCount: Number(sample.replacedContainedSnippetCount || 0),
    });
  }

  function extractCommentTextDetails(container) {
    const snippets = [];
    const seen = new Set();
    const diagnostics = {
      candidateCount: 0,
      includedCount: 0,
      reasonCounts: {},
      samples: [],
    };
    for (const node of container.querySelectorAll(commentTextCandidates)) {
      const rawNodeText = normalizeText(node.innerText || node.textContent || "");
      const text = cleanCommentExtractedText(rawNodeText);
      diagnostics.candidateCount += 1;
      const rejectionReason = getCommentTextNodeRejectionReason(text, node);
      if (rejectionReason) {
        pushCommentTextDiagnosticSample(diagnostics, {
          reason: rejectionReason,
          included: false,
          text,
          rawText: rawNodeText,
          node,
        });
        continue;
      }
      const addResult = addTextSnippetWithOverlap(snippets, seen, text);
      pushCommentTextDiagnosticSample(diagnostics, {
        reason: addResult.reason,
        included: addResult.included,
        text,
        rawText: rawNodeText,
        node,
        isContainedByPreviousSnippet: addResult.isContainedByPreviousSnippet,
        containsPreviousSnippet: addResult.containsPreviousSnippet,
        replacedContainedSnippetCount: addResult.replacedContainedSnippetCount,
      });
      if (!addResult.included) continue;
      if (snippets.length >= 6) break;
    }
    if (snippets.length) {
      const rawText = normalizeText(snippets.join(" "));
      const text = cleanCommentExtractedText(rawText);
      diagnostics.finalTextLength = text.length;
      diagnostics.rawTextLength = rawText.length;
      return { text, rawText, source: "comment", textDiagnostics: diagnostics };
    }
    const rawText = normalizeText(container.innerText || container.textContent || "");
    const text = cleanCommentExtractedText(rawText);
    diagnostics.finalTextLength = text.length;
    diagnostics.rawTextLength = rawText.length;
    return { text, rawText, source: "container", textDiagnostics: diagnostics };
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
  let filteredOutOfScopeCount = 0;
  const searchRoots = collectCommentSearchRoots();

  for (const root of searchRoots.roots) {
    for (const anchor of root.querySelectorAll(commentPermalinkAnchors)) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      if (!isVisibleElement(anchor)) continue;
      const href = anchor.href || anchor.getAttribute("href") || "";
      const commentId = extractCommentIdFromValue(href);
      if (!commentId || seen.has(commentId)) continue;
      const container = findCommentContainerFromPermalinkAnchor(anchor);
      if (!(container instanceof HTMLElement) || !isVisibleElement(container)) continue;
      candidateCount += 1;

      const url = normalizeFacebookUrl(href);
      const routePostId = extractGroupPostRouteIdFromUrl(url, scanTarget.groupId);
      const scope = evaluateCommentTargetScope({ routePostId, root, searchRoots });
      if (!scope.accepted) {
        filteredNonPostCount += 1;
        filteredOutOfScopeCount += 1;
        continue;
      }
      seen.add(commentId);

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
        textDiagnostics: textDetails.textDiagnostics,
        commentAnchorHref: buildDiagnosticCommentHref(href),
        routePostId,
        routePostIdMatchesTarget: Boolean(scope.routePostIdMatchesTarget),
        routePostIdSource: scope.routePostIdSource,
        commentScopeReason: scope.reason,
        commentSearchRoot: getCommentSearchRootLabel(root),
        commentSearchRootStrategy: searchRoots.strategy,
        currentRoutePostId: searchRoots.currentRoutePostId,
        currentRouteMatchesTarget: Boolean(searchRoots.currentRouteMatchesTarget),
        linkCount: container.querySelectorAll("a[href]").length,
        source: "comment_permalink_anchor",
        containerRole: container.matches('[role="article"]') ? "article" : "comment_container",
      });
      if (items.length >= limit) break;
    }
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
      filteredOutOfScopeCount,
      commentSearchRootStrategy: searchRoots.strategy,
      currentRoutePostId: searchRoots.currentRoutePostId,
      currentRouteMatchesTarget: Boolean(searchRoots.currentRouteMatchesTarget),
      stopReason: "visible_window_completed",
    },
  };
}
"""
