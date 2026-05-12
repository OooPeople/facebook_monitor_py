"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_PERMALINK_SCRIPT = '''            const buildCanonicalGroupPostUrl = (groupId, postId) => {
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
            const isExpectedGroupHomeHref = (value, expectedGroupId = "") => {
                const url = normalizeFacebookUrl(value);
                if (!url) return false;
                const pathname = url.pathname.replace(/\\/+$/, "");
                const match = pathname.match(/^\\/groups\\/([^/?#]+)$/i);
                if (!match) return false;
                return !expectedGroupId || match[1] === expectedGroupId;
            };
            const isFacebookHomeHref = (value) => {
                const url = normalizeFacebookUrl(value);
                if (!url) return false;
                const pathname = url.pathname.replace(/\\/+$/, "");
                return pathname === "";
            };
            const isLikelyObfuscatedTimestampAnchorText = (value) => {
                const compact = normalizeText(value).replace(/\\s+/g, "");
                if (!compact) return false;
                return (
                    /(?:剛剛|昨天|今天|Now)/iu.test(compact) ||
                    /\\d{1,4}.*(?:分鐘|小時|天|週|個月|月|年|分|時)/u.test(compact) ||
                    /\\d{1,4}.*(?:m|min|h|hr|hrs|d|w|mo|y)/i.test(compact)
                );
            };
            const isLikelyHeaderTimestampWarmupAnchor = (
                anchor,
                href,
                text,
                relativeTop,
                expectedGroupId = ""
            ) => {
                if (!(anchor instanceof HTMLAnchorElement)) return false;
                if (anchor.getAttribute("aria-hidden") === "true") return false;
                if (anchor.getAttribute("role") !== "link") return false;
                if (relativeTop < -16 || relativeTop > 140) return false;
                if (!isExpectedGroupHomeHref(href, expectedGroupId)) return false;
                const rawHref = anchor.getAttribute("href") || "";
                if (
                    rawHref &&
                    !isFacebookHomeHref(rawHref) &&
                    !isExpectedGroupHomeHref(rawHref, expectedGroupId)
                ) {
                    return false;
                }
                const rect = anchor.getBoundingClientRect();
                if (rect.height > 32 || rect.width > 96) return false;
                return isLikelyObfuscatedTimestampAnchorText(text);
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
'''

__all__ = ["FEED_DOM_PERMALINK_SCRIPT"]
