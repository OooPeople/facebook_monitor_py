"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_COLLECTOR_SCRIPT = r'''  const items = [];
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
'''

__all__ = ["COMMENT_DOM_COLLECTOR_SCRIPT"]
