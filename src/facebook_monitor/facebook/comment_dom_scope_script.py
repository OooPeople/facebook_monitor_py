"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_SCOPE_SCRIPT = r'''  function getCurrentRoutePostId() {
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

'''

__all__ = ["COMMENT_DOM_SCOPE_SCRIPT"]
