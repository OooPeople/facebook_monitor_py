"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_SCOPE_SCRIPT = '''            const findFeedChildContainer = (node) => {
                if (!(node instanceof HTMLElement)) return null;
                const feed = document.querySelector('[role="feed"]');
                if (!(feed instanceof HTMLElement)) return null;
                let current = node;
                while (current && current instanceof HTMLElement) {
                    if (current.parentElement === feed) return current;
                    current = current.parentElement;
                }
                return null;
            };
            const findPermalinkAnchorDrivenPostElement = (node, expectedGroupId = getCurrentGroupId()) => {
                if (!(node instanceof HTMLElement)) return null;
                const primaryCandidate = collectCanonicalPermalinkCandidates(
                    node,
                    expectedGroupId,
                    { upperRegionOnly: true }
                )[0] || null;
                if (!primaryCandidate?.anchor) return null;
                const article = primaryCandidate.anchor.closest('[role="article"]');
                if (article instanceof HTMLElement) return article;
                return findFeedChildContainer(primaryCandidate.anchor);
            };
            const collectPermalinkSearchScopes = (container) => {
                if (!(container instanceof HTMLElement)) return [];
                const scopes = [];
                const seen = new Set();
                const addScope = (node, label, diagnosticOnly = false) => {
                    if (!(node instanceof HTMLElement) || seen.has(node)) return;
                    seen.add(node);
                    scopes.push({ node, label, diagnosticOnly });
                };
                addScope(container, "container");
                const shouldInspectNestedArticles = container.matches('[role="article"]');
                const permalinkDriven = findPermalinkAnchorDrivenPostElement(container);
                if (
                    shouldInspectNestedArticles &&
                    permalinkDriven instanceof HTMLElement &&
                    permalinkDriven !== container
                ) {
                    addScope(permalinkDriven, "permalink_focus");
                }
                if (shouldInspectNestedArticles) {
                    let nestedArticleIndex = 0;
                    for (const article of container.querySelectorAll('[role="article"]')) {
                        if (!(article instanceof HTMLElement)) continue;
                        nestedArticleIndex += 1;
                        addScope(article, `nested_article_${nestedArticleIndex}`);
                        if (nestedArticleIndex >= 2) break;
                    }
                }
                const closestArticle = container.closest('[role="article"]');
                if (closestArticle instanceof HTMLElement && closestArticle !== container) {
                    addScope(closestArticle, "closest_article");
                }
                const parent = container.parentElement;
                if (parent instanceof HTMLElement) {
                    addScope(parent, "parent", true);
                }
                return scopes;
            };
            const getCurrentGroupId = () => {
                const match = location.pathname.match(/^\\/groups\\/([^/?#]+)/i);
                return match ? decodeURIComponent(match[1]) : "";
            };
            const extractPostIdFromPermalink = (permalink) => {
                const match = String(permalink || "").match(/\\/posts\\/(\\d{8,})(?:$|[/?#])/i);
                return match ? match[1] : "";
            };
            const extractPermalinkDetails = (container) => {
                if (!(container instanceof HTMLElement)) {
                    return { ...buildPermalinkDetails(), canonicalCandidateCount: 0 };
                }
                const expectedGroupId = getCurrentGroupId();
                const scopes = collectPermalinkSearchScopes(container);
                let canonicalCandidateCount = 0;
                for (const scope of scopes) {
                    if (scope.diagnosticOnly) continue;
                    const canonicalCandidates = collectCanonicalPermalinkCandidates(
                        scope.node,
                        expectedGroupId,
                        { upperRegionOnly: scope.label === "container" && !container.matches('[role="article"]') }
                    );
                    canonicalCandidateCount += canonicalCandidates.length;
                    for (const candidate of canonicalCandidates) {
                        if (candidate.permalink) {
                            return {
                                permalink: candidate.permalink,
                                source: `${scope.label}:${candidate.source}`,
                                canonicalCandidateCount,
                            };
                        }
                    }
                    const genericAnchors = collectAnchorsFromScope(scope.node, "a[href]", {
                        excludeUserProfile: true,
                    });
                    for (const anchor of genericAnchors) {
                        const details = extractCanonicalPermalinkFromHref(
                            anchor.href || anchor.getAttribute("href") || "",
                            expectedGroupId
                        );
                        if (details.permalink) {
                            return {
                                permalink: details.permalink,
                                source: `${scope.label}:${details.source}`,
                                canonicalCandidateCount,
                            };
                        }
                    }
                }
                return { ...buildPermalinkDetails(), canonicalCandidateCount };
            };
            const getCanonicalPostElement = (node) => {
                if (!(node instanceof HTMLElement)) return null;

                const feedChild = findFeedChildContainer(node);
                if (feedChild instanceof HTMLElement) return feedChild;

                const permalinkDriven = findPermalinkAnchorDrivenPostElement(node);
                if (permalinkDriven instanceof HTMLElement) return permalinkDriven;

                if (node.matches('[role="article"]')) return node;
                const article = node.closest('[role="article"]');
                if (article instanceof HTMLElement) return article;
                return node;
            };
            const getContainerRole = (node) => {
                if (!(node instanceof HTMLElement)) return "";
                if (node.matches('[role="article"]')) return "article";
                if (findFeedChildContainer(node) === node) return "feed_child";
                return "node";
            };

'''

__all__ = ["FEED_DOM_SCOPE_SCRIPT"]
