"""Facebook feed DOM extraction scripts.

職責：集中保存短生命週期 page.evaluate 使用的 DOM 抽取腳本，避免
`feed_extractor.py` 同時承擔 Python orchestration 與大型 JavaScript 字串。
"""

from __future__ import annotations


POST_LIKE_ITEMS_SCRIPT = """async (maxItems) => {
            const feedRoots = [
                '[role="feed"]',
                'div[data-pagelet*="GroupsFeed"]',
                'div[data-pagelet*="FeedUnit"]',
                '[role="main"]',
            ];
            const postContainerCandidates = [
                'a[href*="/groups/"][href*="/posts/"], a[href*="/groups/"][href*="/post/"], a[href*="/permalink/"], a[href*="multi_permalinks="], a[href*="story_fbid="], a[href*="set=gm."]',
                '[role="feed"] [role="article"]',
                '[role="feed"] > div',
                'div[data-pagelet*="FeedUnit"]',
                'div[data-pagelet*="GroupsFeed"] [role="article"]',
                '[aria-posinset]',
            ];
            const postPermalinkAnchors =
                'a[href*="/groups/"][href*="/posts/"], a[href*="/groups/"][href*="/post/"], a[href*="/permalink/"], a[href*="multi_permalinks="], a[href*="story_fbid="], a[href*="set=gm."]';
            const commentPermalinkAnchors = 'a[href*="comment_id="], a[href*="reply_comment_id="]';
            const postStoryMessage =
                'div[data-ad-comet-preview="message"], div[data-ad-preview="message"], [data-ad-rendering-role="story_message"]';
            const minCandidateTextLength = 8;
            const authorSelectors = [
                'h2 span',
                'h3 span',
                'a[role="link"] span[dir="auto"]',
                'strong span'
            ];
            const authorUiLabels = /^(Like|Comment|Share|Most relevant|讚|留言|分享|最相關)$/i;
            const commentActionTrail = [
                /(?:^|\\s)(?:剛剛|昨天|今天|now|\\d+\\s*(?:分鐘|小時|天|週|個月|月|年|m|min|h|hr|hrs|d|w|mo|y)\\s*(?:前)?)?\\s*(?:讚|like)\\s+(?:回覆|reply)(?:\\s|$)/iu,
            ];
            const noisyTextFragments = [
                "Facebook",
                "貼文的相片",
                "顯示更多",
                "查看更多",
                "See more",
                "Most relevant",
                "Like",
                "Comment",
                "Share",
            ];
            const cleanedTextNoise = [
                /\\b[a-z0-9]{12,}\\.com\\b/gi,
                /\\bsnproSet[a-z0-9]+\\b/gi,
                /\\bsotoeSrdpn[a-z0-9]+\\b/gi,
            ];
            const normalizeText = (value) => String(value || "")
                .replace(/[\\u200B-\\u200D\\uFEFF]/g, "")
                .replace(/\\s+/g, " ")
                .trim();
            const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
            const isVisible = (element) => {
                if (!(element instanceof HTMLElement)) return false;
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const isCommentPermalinkHref = (href) =>
                /[?&](?:comment_id|reply_comment_id)=/i.test(String(href || ""));
            const isPostPermalinkHref = (href) => {
                const value = String(href || "");
                return !isCommentPermalinkHref(value) && (
                    value.includes('/posts/') ||
                    value.includes('/post/') ||
                    value.includes('/permalink/') ||
                    value.includes('multi_permalinks=') ||
                    value.includes('story_fbid=') ||
                    value.includes('set=gm.')
                );
            };
            const hasCommentActionTrail = (value) => {
                const text = normalizeText(value);
                return Boolean(text && commentActionTrail.some((pattern) => pattern.test(text)));
            };
            const stripCommentActionTrail = (value) => {
                let text = String(value || "");
                if (!text) return "";
                for (const pattern of commentActionTrail) {
                    text = text.replace(pattern, " ");
                }
                return normalizeText(text);
            };
            const collapseRepeatedAdjacentText = (value) => {
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
            };
            const cleanExtractedText = (value) => {
                let text = stripCommentActionTrail(value);
                if (!text) return "";
                for (const fragment of noisyTextFragments) {
                    text = text.replaceAll(fragment, " ");
                }
                for (const pattern of cleanedTextNoise) {
                    text = text.replace(pattern, " ");
                }
                return collapseRepeatedAdjacentText(text.replace(/\\s+/g, " ").trim());
            };
            const isFeedSortControlText = (value) =>
                normalizeText(value).includes("社團動態消息排序方式");
            const getElementText = (element) => normalizeText(
                element?.innerText ||
                element?.textContent ||
                element?.getAttribute?.("aria-label") ||
                ""
            );
            const sortElementsByViewportTop = (elements) => {
                return [...elements].sort((left, right) => {
                    return Math.round(left.getBoundingClientRect().top) -
                        Math.round(right.getBoundingClientRect().top);
                });
            };
            const collectUniqueTextSnippets = (container, selectors, minLength, maxItems, upperOnly) => {
                const snippets = [];
                const seen = new Set();
                const containerRect = container.getBoundingClientRect();
                for (const selector of selectors) {
                    for (const node of Array.from(container.querySelectorAll(selector))) {
                        if (!(node instanceof HTMLElement)) continue;
                        if (upperOnly) {
                            const rect = node.getBoundingClientRect();
                            const relativeTop = rect.top - containerRect.top;
                            const upperLimit = Math.max(210, containerRect.height * 0.46);
                            if (relativeTop > upperLimit) continue;
                        }
                        const text = cleanExtractedText(node.innerText || node.textContent || "");
                        if (text.length < minLength || seen.has(text)) continue;
                        seen.add(text);
                        snippets.push(text);
                        if (snippets.length >= maxItems) return snippets;
                    }
                }
                return snippets;
            };
            const extractPostTextDetails = (container) => {
                const primarySnippets = collectUniqueTextSnippets(
                    container,
                    [postStoryMessage],
                    2,
                    8,
                    false
                );
                if (primarySnippets.length) {
                    const rawText = normalizeText(primarySnippets.join(" "));
                    return { text: cleanExtractedText(rawText), rawText, source: "primary" };
                }

                const fallbackSnippets = collectUniqueTextSnippets(
                    container,
                    ['div[dir="auto"]', 'span[dir="auto"]'],
                    6,
                    8,
                    true
                );
                if (fallbackSnippets.length) {
                    const rawText = normalizeText(fallbackSnippets.join(" "));
                    return { text: cleanExtractedText(rawText), rawText, source: "fallback" };
                }

                const rawText = normalizeText(container.innerText || "");
                return { text: cleanExtractedText(rawText), rawText, source: "container" };
            };
            const extractAuthor = (node) => {
                for (const selector of authorSelectors) {
                    const candidates = Array.from(node.querySelectorAll(selector));
                    for (const candidate of candidates) {
                        const text = (candidate.innerText || "").replace(/\\s*[·•]\\s*追蹤\\s*$/u, "").trim();
                        if (!text || text.length > 80 || authorUiLabels.test(text)) {
                            continue;
                        }
                        return text;
                    }
                }
                return "";
            };
            const isPostTextExpander = (element, container) => {
                if (!(element instanceof HTMLElement) || !(container instanceof HTMLElement)) return false;
                if (!isVisible(element)) return false;
                const text = getElementText(element);
                if (!["顯示更多", "查看更多", "See more"].includes(text)) return false;
                const containerRect = container.getBoundingClientRect();
                const elementRect = element.getBoundingClientRect();
                const relativeTop = elementRect.top - containerRect.top;
                const upperRegionThreshold = Math.max(220, Math.round(containerRect.height * 0.72));
                return relativeTop >= -12 && relativeTop <= upperRegionThreshold;
            };
            const findPostTextExpanders = (container) => {
                if (!(container instanceof HTMLElement)) return [];
                const results = [];
                const seen = new Set();
                for (const selector of ['div[role="button"]', 'span[role="button"]', 'a[role="button"]', "button"]) {
                    for (const node of container.querySelectorAll(selector)) {
                        if (!isPostTextExpander(node, container)) continue;
                        if (seen.has(node)) continue;
                        seen.add(node);
                        results.push(node);
                    }
                }
                return sortElementsByViewportTop(results);
            };
            const expandCollapsedPostText = async (container) => {
                if (!(container instanceof HTMLElement)) {
                    return { expandAttempted: false, expandCount: 0 };
                }
                let expandCount = 0;
                for (let attempt = 0; attempt < 2; attempt += 1) {
                    const expanders = findPostTextExpanders(container);
                    if (!expanders.length) break;
                    expanders[0].click();
                    expandCount += 1;
                    await sleep(220);
                }
                return {
                    expandAttempted: expandCount > 0,
                    expandCount,
                };
            };
            const buildCanonicalGroupPostUrl = (groupId, postId) => {
                const normalizedGroupId = String(groupId || "").trim();
                const normalizedPostId = String(postId || "").trim();
                if (!normalizedGroupId || !/^\\d{8,}$/.test(normalizedPostId)) return "";
                return `https://www.facebook.com/groups/${normalizedGroupId}/posts/${normalizedPostId}`;
            };
            const normalizeFacebookUrl = (value) => {
                const text = String(value || "").trim();
                if (!text) return null;
                try {
                    const url = new URL(text, location.origin);
                    if (!/^(www|m)\\.facebook\\.com$/i.test(url.hostname)) return null;
                    return url;
                } catch (error) {
                    return null;
                }
            };
            const buildPermalinkDetails = (permalink = "", source = "unavailable") => ({
                permalink: String(permalink || ""),
                source: String(source || "unavailable"),
            });
            const buildGroupScopedPermalinkDetails = (groupId, postId, source, expectedGroupId = "") => {
                const normalizedGroupId = String(groupId || "").trim();
                const normalizedPostId = String(postId || "").trim();
                if (!normalizedGroupId || !normalizedPostId) return buildPermalinkDetails("", "");
                if (expectedGroupId && normalizedGroupId !== expectedGroupId) {
                    return buildPermalinkDetails("", "");
                }
                const permalink = buildCanonicalGroupPostUrl(normalizedGroupId, normalizedPostId);
                return permalink ? buildPermalinkDetails(permalink, source) : buildPermalinkDetails("", "");
            };
            const extractFirstPatternMatch = (values, patterns) => {
                for (const value of values) {
                    const text = String(value || "");
                    if (!text) continue;
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) return match[1] || "";
                    }
                }
                return "";
            };
            const extractGroupRouteQueryPostId = (url) => {
                if (!(url instanceof URL)) return "";
                return extractFirstPatternMatch(
                    [
                        url.searchParams.get("story_fbid"),
                        url.searchParams.get("multi_permalinks"),
                        url.searchParams.get("set"),
                    ],
                    [
                        /\\b(\\d{8,})\\b/,
                        /\\bgm\\.(\\d+)/i,
                    ]
                );
            };
            const extractPhotoRouteGroupId = (url, expectedGroupId = "") => {
                if (!(url instanceof URL)) return "";
                const groupId = String(
                    url.searchParams.get("idorvanity") ||
                    url.searchParams.get("group") ||
                    url.searchParams.get("group_id") ||
                    url.searchParams.get("id") ||
                    expectedGroupId ||
                    ""
                ).trim();
                if (expectedGroupId && groupId !== expectedGroupId) return "";
                return groupId;
            };
            const extractPhotoRoutePermalinkDetails = (url, expectedGroupId = "") => {
                if (!(url instanceof URL)) return buildPermalinkDetails("", "");
                return buildGroupScopedPermalinkDetails(
                    extractPhotoRouteGroupId(url, expectedGroupId),
                    extractGroupRouteQueryPostId(url),
                    "photo_gm_anchor",
                    expectedGroupId
                );
            };
            const getPermalinkSourcePriority = (source = "") => {
                if (source === "groups_post_anchor") return 0;
                if (source === "group_permalink_anchor") return 1;
                if (source === "permalink_php_anchor") return 2;
                if (source === "group_query_anchor") return 3;
                if (source === "pcb_anchor") return 4;
                return 5;
            };
            const extractCanonicalPermalinkFromHref = (value, expectedGroupId = "") => {
                const url = normalizeFacebookUrl(value);
                if (!url) return buildPermalinkDetails("", "");

                const pathname = url.pathname.replace(/\\/+$/, "");
                const groupPostMatch = pathname.match(/^\\/groups\\/([^/?#]+)\\/posts?\\/(\\d+)$/i);
                if (groupPostMatch) {
                    const [, groupId, postId] = groupPostMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        postId,
                        "groups_post_anchor",
                        expectedGroupId
                    );
                }

                const groupPermalinkMatch = pathname.match(/^\\/groups\\/([^/?#]+)\\/permalink\\/(\\d+)$/i);
                if (groupPermalinkMatch) {
                    const [, groupId, postId] = groupPermalinkMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        postId,
                        "group_permalink_anchor",
                        expectedGroupId
                    );
                }

                const pcbMatch = pathname.match(/^\\/groups\\/([^/?#]+)\\/posts\\/pcb\\.(\\d+)$/i);
                if (pcbMatch) {
                    const [, groupId, postId] = pcbMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        postId,
                        "pcb_anchor",
                        expectedGroupId
                    );
                }

                if (/^\\/photo(?:\\.php)?$/i.test(pathname)) {
                    return extractPhotoRoutePermalinkDetails(url, expectedGroupId);
                }

                const groupRouteMatch = pathname.match(/^\\/groups\\/([^/?#]+)(?:\\/.*)?$/i);
                if (groupRouteMatch) {
                    const [, groupId] = groupRouteMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        extractGroupRouteQueryPostId(url),
                        "group_query_anchor",
                        expectedGroupId
                    );
                }

                if (!/^\\/permalink\\.php$/i.test(pathname)) return buildPermalinkDetails("", "");
                return buildGroupScopedPermalinkDetails(
                    String(url.searchParams.get("id") || url.searchParams.get("group_id") || expectedGroupId || "").trim(),
                    extractGroupRouteQueryPostId(url),
                    "permalink_php_anchor",
                    expectedGroupId
                );
            };
            const isLikelyUserProfileHref = (value) => {
                const url = normalizeFacebookUrl(value);
                if (!url) return false;
                const pathname = url.pathname.replace(/\\/+$/, "");
                if (/^\\/groups\\/[^/?#]+\\/user\\/[^/?#]+$/i.test(pathname)) return true;
                if (/^\\/profile\\.php$/i.test(pathname) && url.searchParams.get("id")) return true;
                return false;
            };
            const isLikelyTimestampAnchorText = (value) => {
                const text = normalizeText(value);
                if (!text) return false;
                return (
                    /^(?:剛剛|昨天|今天|Now)$/u.test(text) ||
                    /^\\d+\\s*(?:分鐘|小時|天|週|個月|月|年)\\s*前$/u.test(text) ||
                    /^\\d+\\s*(?:m|min|h|hr|hrs|d|w|mo|y)\\s*$/i.test(text) ||
                    /^\\d{1,2}:\\d{2}(?:\\s*[AP]M)?$/i.test(text) ||
                    /^(?:\\d{4}年)?\\d{1,2}月\\d{1,2}日(?:\\s*[\\d:APMapm]+)?$/u.test(text)
                );
            };
            const isLikelyWarmupUtilityHref = (value, expectedGroupId = "") => {
                const url = normalizeFacebookUrl(value);
                if (!url) return true;
                const pathname = url.pathname.replace(/\\/+$/, "");
                if (/^\\/hashtag\\//i.test(pathname)) return true;
                if (
                    /^\\/groups\\/[^/?#]+$/i.test(pathname) &&
                    !url.searchParams.get("story_fbid") &&
                    !url.searchParams.get("multi_permalinks") &&
                    !url.searchParams.get("set")
                ) {
                    return true;
                }
                if (/^\\/l\\.php$/i.test(pathname)) return true;
                if (expectedGroupId && /^\\/groups\\/([^/?#]+)(?:\\/.*)?$/i.test(pathname)) {
                    const match = pathname.match(/^\\/groups\\/([^/?#]+)(?:\\/.*)?$/i);
                    if (match && match[1] !== expectedGroupId) return true;
                }
                return false;
            };
            const isElementInContainerUpperRegion = (element, container) => {
                if (!(element instanceof HTMLElement) || !(container instanceof HTMLElement)) return false;
                const containerRect = container.getBoundingClientRect();
                const elementRect = element.getBoundingClientRect();
                const relativeTop = elementRect.top - containerRect.top;
                const upperLimit = Math.max(210, containerRect.height * 0.46);
                return relativeTop >= -16 && relativeTop <= upperLimit;
            };
            const collectAnchorsFromScope = (scopeNode, selector = "a[href]", options = {}) => {
                if (!(scopeNode instanceof HTMLElement)) return [];
                const { excludeUserProfile = false, maxItems = Number.POSITIVE_INFINITY } = options;
                const anchors = [];
                const seen = new Set();
                const pushAnchor = (anchor) => {
                    if (!(anchor instanceof HTMLAnchorElement)) return;
                    const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                    if (!href || seen.has(href)) return;
                    if (excludeUserProfile && isLikelyUserProfileHref(href)) return;
                    seen.add(href);
                    anchors.push(anchor);
                };
                if (scopeNode instanceof HTMLAnchorElement && scopeNode.matches(selector)) {
                    pushAnchor(scopeNode);
                }
                for (const anchor of scopeNode.querySelectorAll(selector)) {
                    pushAnchor(anchor);
                    if (anchors.length >= maxItems) break;
                }
                return anchors;
            };
            const collectCanonicalPermalinkCandidates = (scopeNode, expectedGroupId = "", options = {}) => {
                if (!(scopeNode instanceof HTMLElement)) return [];
                const { upperRegionOnly = false } = options;
                const candidates = [];
                const seen = new Set();
                for (const anchor of collectAnchorsFromScope(scopeNode, postPermalinkAnchors)) {
                    if (upperRegionOnly && !isElementInContainerUpperRegion(anchor, scopeNode)) continue;
                    const href = anchor.href || anchor.getAttribute("href") || "";
                    const details = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                    if (!details.permalink || seen.has(details.permalink)) continue;
                    seen.add(details.permalink);
                    candidates.push({
                        anchor,
                        href,
                        permalink: details.permalink,
                        source: details.source,
                        isCommentLink: isCommentPermalinkHref(href),
                    });
                }
                candidates.sort((a, b) => {
                    if (a.isCommentLink !== b.isCommentLink) return a.isCommentLink ? 1 : -1;
                    const sourceDiff = getPermalinkSourcePriority(a.source) - getPermalinkSourcePriority(b.source);
                    if (sourceDiff !== 0) return sourceDiff;
                    const topDiff = Math.round(a.anchor.getBoundingClientRect().top) - Math.round(b.anchor.getBoundingClientRect().top);
                    if (topDiff !== 0) return topDiff;
                    return a.href.length - b.href.length;
                });
                return candidates;
            };
            const collectPermalinkWarmupAnchors = (container, expectedGroupId = getCurrentGroupId(), limit = 4) => {
                if (!(container instanceof HTMLElement)) return [];
                const anchors = [];
                const seen = new Set();
                const containerRect = container.getBoundingClientRect();
                const upperRegionThreshold = Math.max(180, Math.round(containerRect.height * 0.38));
                for (const anchor of collectAnchorsFromScope(container, 'a[role="link"], a[href]')) {
                    if (!(anchor instanceof HTMLAnchorElement)) continue;
                    if (!isVisible(anchor)) continue;
                    const href = String(anchor.href || anchor.getAttribute("href") || "").trim();
                    const text = normalizeText(
                        anchor.innerText ||
                        anchor.textContent ||
                        anchor.getAttribute("aria-label") ||
                        ""
                    );
                    const relativeTop = anchor.getBoundingClientRect().top - containerRect.top;
                    const canonicalDetails = extractCanonicalPermalinkFromHref(href, expectedGroupId);
                    const likelyTimestamp = isLikelyTimestampAnchorText(text);
                    const hasAttributionSrc = anchor.hasAttribute("attributionsrc");
                    if (relativeTop < -16 || relativeTop > upperRegionThreshold) continue;
                    if (isLikelyUserProfileHref(href)) continue;
                    if (
                        !canonicalDetails.permalink &&
                        isLikelyWarmupUtilityHref(href, expectedGroupId) &&
                        !likelyTimestamp &&
                        !hasAttributionSrc
                    ) {
                        continue;
                    }
                    const signature = `${href}||${text}||${Math.round(relativeTop)}`;
                    if (seen.has(signature)) continue;
                    seen.add(signature);
                    anchors.push({
                        anchor,
                        href,
                        text,
                        relativeTop,
                        canonicalDetails,
                        likelyTimestamp,
                        hasAttributionSrc,
                    });
                }
                anchors.sort((a, b) => {
                    if (Boolean(a.canonicalDetails.permalink) !== Boolean(b.canonicalDetails.permalink)) {
                        return a.canonicalDetails.permalink ? -1 : 1;
                    }
                    if (a.likelyTimestamp !== b.likelyTimestamp) {
                        return a.likelyTimestamp ? -1 : 1;
                    }
                    if (a.hasAttributionSrc !== b.hasAttributionSrc) {
                        return a.hasAttributionSrc ? -1 : 1;
                    }
                    return Math.round(a.relativeTop) - Math.round(b.relativeTop);
                });
                return anchors.slice(0, limit);
            };
            const dispatchPermalinkWarmupEvents = (anchor) => {
                if (!(anchor instanceof HTMLElement)) return;
                const eventInit = {
                    bubbles: true,
                    cancelable: true,
                    composed: true,
                    view: window,
                };
                try {
                    anchor.dispatchEvent(new MouseEvent("mouseenter", eventInit));
                    anchor.dispatchEvent(new MouseEvent("mouseover", eventInit));
                    anchor.dispatchEvent(new MouseEvent("mousemove", eventInit));
                } catch (error) {
                    // 忽略事件建立失敗，改走 focus fallback。
                }
                try {
                    anchor.dispatchEvent(new PointerEvent("pointerenter", eventInit));
                    anchor.dispatchEvent(new PointerEvent("pointerover", eventInit));
                } catch (error) {
                    // 某些執行環境未必支援 PointerEvent。
                }
                try {
                    anchor.focus({ preventScroll: true });
                } catch (error) {
                    try {
                        anchor.focus();
                    } catch (focusError) {
                        // 忽略 focus 失敗。
                    }
                }
            };
            const buildPermalinkWarmupState = ({
                warmupAttempted = false,
                warmupResolved = false,
                warmupCandidateCount = 0,
            } = {}) => ({
                warmupAttempted: Boolean(warmupAttempted),
                warmupResolved: Boolean(warmupResolved),
                warmupCandidateCount: Number(warmupCandidateCount) || 0,
            });
            const warmPermalinkAnchors = async (container) => {
                if (!(container instanceof HTMLElement)) return buildPermalinkWarmupState();
                const expectedGroupId = getCurrentGroupId();
                const warmupAnchors = collectPermalinkWarmupAnchors(container, expectedGroupId);
                if (!warmupAnchors.length) return buildPermalinkWarmupState();
                let warmupAttempted = false;
                for (const candidate of warmupAnchors) {
                    if (candidate.canonicalDetails.permalink) {
                        return buildPermalinkWarmupState({
                            warmupAttempted,
                            warmupResolved: true,
                            warmupCandidateCount: warmupAnchors.length,
                        });
                    }
                    warmupAttempted = true;
                    dispatchPermalinkWarmupEvents(candidate.anchor);
                    await sleep(90);
                    const refreshedHref = candidate.anchor.href || candidate.anchor.getAttribute("href") || "";
                    const refreshedDetails = extractCanonicalPermalinkFromHref(refreshedHref, expectedGroupId);
                    if (refreshedDetails.permalink) {
                        return buildPermalinkWarmupState({
                            warmupAttempted,
                            warmupResolved: true,
                            warmupCandidateCount: warmupAnchors.length,
                        });
                    }
                }
                return buildPermalinkWarmupState({
                    warmupAttempted,
                    warmupResolved: false,
                    warmupCandidateCount: warmupAnchors.length,
                });
            };
            const findFeedChildContainer = (node) => {
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

            const roots = [];
            const seenRoots = new Set();
            for (const selector of feedRoots) {
                for (const root of Array.from(document.querySelectorAll(selector))) {
                    if (!(root instanceof HTMLElement) || seenRoots.has(root)) continue;
                    seenRoots.add(root);
                    roots.push(root);
                }
            }
            const searchRoots = roots.length ? roots : [document];

            const nodes = [];
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
                        nodes.push(canonical);
                    }
                }
            }

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
            for (const node of nodes) {
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
                const hasStoryMessage = node.querySelector(postStoryMessage) instanceof HTMLElement;
                const hasCommentPermalink = node.querySelector(commentPermalinkAnchors) instanceof HTMLAnchorElement;
                const containerRole = getContainerRole(node);
                const links = Array.from(node.querySelectorAll('a[href]'))
                    .map((anchor) => anchor.href || anchor.getAttribute('href') || "")
                    .filter(Boolean);

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
                    textSource: textDetails.source,
                    rawTextLength: rawText.length,
                    permalinkSource: permalinkDetails.source || "unavailable",
                    canonicalPermalinkCandidateCount: Number(permalinkDetails.canonicalCandidateCount) || 0,
                    postId,
                    postIdSource: postId ? "permalink" : "none",
                    hasStoryMessage,
                    hasCommentPermalink,
                    warmupAttempted: Boolean(warmupState.warmupAttempted),
                    warmupResolved: Boolean(warmupState.warmupResolved),
                    warmupCandidateCount: Number(warmupState.warmupCandidateCount) || 0,
                    expandAttempted: Boolean(expandState.expandAttempted),
                    expandCount: Number(expandState.expandCount) || 0,
                });
                if (results.length >= maxItems) break;
            }

            const items = results.filter((item) => item && item.textLength > 0).slice(0, maxItems);
            meta.parsedCount = items.length;
            return { items, meta };
        }"""
