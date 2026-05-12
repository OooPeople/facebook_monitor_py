"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_COLLECTOR_SCRIPT = r'''            const roots = [];
            const seenRoots = new Set();
            for (const selector of feedRoots) {
                for (const root of Array.from(document.querySelectorAll(selector))) {
                    if (!(root instanceof HTMLElement) || seenRoots.has(root)) continue;
                    seenRoots.add(root);
                    roots.push(root);
                }
            }
            const searchRoots = roots.length ? roots : [document];

            const candidateNodes = [];
            const seenNodes = new Set();
            for (const root of searchRoots) {
                for (const selector of postContainerCandidates) {
                    for (const node of Array.from(root.querySelectorAll(selector))) {
                        const canonical = getCanonicalPostElement(node);
                        if (!(canonical instanceof HTMLElement)) continue;
                        if (seenNodes.has(canonical)) continue;
                        if (!isVisible(canonical)) continue;
                        const candidateText = normalizeText(canonical.innerText || canonical.textContent || "");
                        if (candidateText.length < minCandidateTextLength) continue;
                        seenNodes.add(canonical);
                        candidateNodes.push(canonical);
                    }
                }
            }
            const nodes = sortElementsByViewportTop(candidateNodes);

            const meta = {
                candidateLimit: Number(maxItems) || 0,
                candidateCount: nodes.length,
                cacheHitCount: 0,
                freshExtractCount: 0,
                parsedCount: 0,
                filteredEmptyTextCount: 0,
                filteredNonPostCount: 0,
                filteredFeedSortControlCount: 0,
                articleElementCount: 0,
                postsWithPostIdCount: 0,
            };
            const results = [];
            for (let nodeIndex = 0; nodeIndex < nodes.length; nodeIndex += 1) {
                const node = nodes[nodeIndex];
                const nodeRect = node.getBoundingClientRect();
                const expandState = await expandCollapsedPostText(node);
                const textDetails = extractPostTextDetails(node);
                const text = normalizeText(textDetails.text);
                const rawText = normalizeText(textDetails.rawText || text);
                let permalinkDetails = extractPermalinkDetails(node);
                let warmupState = buildPermalinkWarmupState({
                    warmupResolved: Boolean(permalinkDetails.permalink),
                });
                if (!permalinkDetails.permalink) {
                    warmupState = await warmPermalinkAnchors(node);
                    if (warmupState.warmupAttempted || warmupState.warmupResolved) {
                        permalinkDetails = extractPermalinkDetails(node);
                    }
                }
                const permalink = permalinkDetails.permalink || "";
                const postId = extractPostIdFromPermalink(permalink);
                const warmupDiagnostics = permalink
                    ? null
                    : collectPermalinkWarmupDiagnostics(node, getCurrentGroupId());
                const hasStoryMessage = node.querySelector(postStoryMessage) instanceof HTMLElement;
                const hasCommentPermalink = node.querySelector(commentPermalinkAnchors) instanceof HTMLAnchorElement;
                const containerRole = getContainerRole(node);
                const links = Array.from(node.querySelectorAll('a[href]'))
                    .map((anchor) => anchor.href || anchor.getAttribute('href') || "")
                    .filter(Boolean);
                const linkDiagnostics = collectLinkDiagnostics(node, getCurrentGroupId());

                meta.freshExtractCount += 1;
                if (containerRole === "article") {
                    meta.articleElementCount += 1;
                }
                if (!text) {
                    meta.filteredEmptyTextCount += 1;
                    continue;
                }
                if (!hasStoryMessage && !permalink) {
                    meta.filteredNonPostCount += 1;
                    continue;
                }
                if (!hasStoryMessage && hasCommentPermalink) {
                    meta.filteredNonPostCount += 1;
                    continue;
                }
                if (isFeedSortControlText(text)) {
                    meta.filteredFeedSortControlCount += 1;
                    continue;
                }
                if (
                    textDetails.source !== "primary" &&
                    containerRole === "article" &&
                    hasCommentActionTrail(rawText)
                ) {
                    meta.filteredNonPostCount += 1;
                    continue;
                }

                if (postId) {
                    meta.postsWithPostIdCount += 1;
                }
                results.push({
                    text,
                    textLength: text.length,
                    permalink,
                    linkCount: links.length,
                    author: extractAuthor(node),
                    source: "feed_dom",
                    containerRole,
                    domIndex: nodeIndex,
                    domPosition: {
                        viewportTop: Math.round(nodeRect.top),
                        documentTop: Math.round(nodeRect.top + window.scrollY),
                        height: Math.round(nodeRect.height),
                    },
                    textSource: textDetails.source,
                    rawTextLength: rawText.length,
                    permalinkSource: permalinkDetails.source || "unavailable",
                    canonicalPermalinkCandidateCount: Number(permalinkDetails.canonicalCandidateCount) || 0,
                    postId,
                    postIdSource: postId ? "permalink" : "none",
                    linkDiagnostics,
                    hasStoryMessage,
                    hasCommentPermalink,
                    warmupAttempted: Boolean(warmupState.warmupAttempted),
                    warmupResolved: Boolean(warmupState.warmupResolved),
                    warmupCandidateCount: Number(warmupState.warmupCandidateCount) || 0,
                    warmupDiagnostics,
                    expandAttempted: Boolean(expandState.expandAttempted),
                    expandCount: Number(expandState.expandCount) || 0,
                });
                if (results.length >= maxItems) break;
            }

            const items = results.filter((item) => item && item.textLength > 0).slice(0, maxItems);
            meta.parsedCount = items.length;
            return { items, meta };
        }'''

__all__ = ["FEED_DOM_COLLECTOR_SCRIPT"]
