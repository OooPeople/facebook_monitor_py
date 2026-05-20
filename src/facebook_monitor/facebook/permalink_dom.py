"""Facebook permalink DOM 共用 JavaScript 片段。

職責：保存 posts/comments DOM extractor 共用的 Facebook URL normalize、
post/comment id 擷取與 canonical permalink 建立規則。Python 端主語義仍由
`facebook.permalink` / `core.permalink_identity` 保護；此檔為 browser
context 必要的鏡像 helper。
"""

PERMALINK_DOM_HELPERS_SCRIPT = r'''
            function buildCanonicalGroupPostUrl(groupId, postId) {
                const normalizedGroupId = String(groupId || "").trim();
                const normalizedPostId = String(postId || "").trim();
                if (!normalizedGroupId || !/^\d{8,}$/.test(normalizedPostId)) return "";
                return `https://www.facebook.com/groups/${normalizedGroupId}/posts/${normalizedPostId}`;
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

            function normalizeFacebookUrl(value) {
                const text = String(value || "").trim();
                if (!text) return null;
                try {
                    const url = new URL(text, location.origin);
                    if (!/^(www|m)\.facebook\.com$/i.test(url.hostname)) return null;
                    return url;
                } catch (error) {
                    return null;
                }
            }

            function buildPermalinkDetails(permalink = "", source = "unavailable") {
                return {
                    permalink: String(permalink || ""),
                    source: String(source || "unavailable"),
                };
            }

            function buildGroupScopedPermalinkDetails(groupId, postId, source, expectedGroupId = "") {
                const normalizedGroupId = String(groupId || "").trim();
                const normalizedPostId = String(postId || "").trim();
                if (!normalizedGroupId || !normalizedPostId) return buildPermalinkDetails("", "");
                if (expectedGroupId && normalizedGroupId !== expectedGroupId) {
                    return buildPermalinkDetails("", "");
                }
                const permalink = buildCanonicalGroupPostUrl(normalizedGroupId, normalizedPostId);
                return permalink ? buildPermalinkDetails(permalink, source) : buildPermalinkDetails("", "");
            }

            function extractFirstPatternMatch(values, patterns) {
                for (const value of values) {
                    const text = String(value || "");
                    if (!text) continue;
                    for (const pattern of patterns) {
                        const match = text.match(pattern);
                        if (match) return match[1] || "";
                    }
                }
                return "";
            }

            function extractCommentIdFromValue(value) {
                return extractFirstPatternMatch(
                    [String(value || "")],
                    [
                        /[?&](?:comment_id|reply_comment_id)=(\d{8,})/i,
                        /\b(?:comment_id|reply_comment_id|feedback_comment_id)["'=:\s]+(\d{8,})/i,
                        /"(?:comment_id|reply_comment_id|feedback_comment_id)":"?(\d+)/i,
                    ]
                );
            }

            function extractGroupRouteQueryPostId(url, preferGmPostId = false) {
                if (!(url instanceof URL)) return "";
                const patterns = preferGmPostId
                    ? [
                        /\bgm\.(\d+)/i,
                        /\b(\d{8,})\b/,
                    ]
                    : [
                        /\b(\d{8,})\b/,
                        /\bgm\.(\d+)/i,
                    ];
                return extractFirstPatternMatch(
                    [
                        url.searchParams.get("story_fbid"),
                        url.searchParams.get("multi_permalinks"),
                        url.searchParams.get("set"),
                    ],
                    patterns
                );
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
                    return extractGroupRouteQueryPostId(url, true);
                }
                return "";
            }

            function extractPhotoRouteGroupId(url, expectedGroupId = "") {
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
            }

            function extractPhotoRoutePermalinkDetails(url, expectedGroupId = "") {
                if (!(url instanceof URL)) return buildPermalinkDetails("", "");
                return buildGroupScopedPermalinkDetails(
                    extractPhotoRouteGroupId(url, expectedGroupId),
                    extractGroupRouteQueryPostId(url),
                    "photo_gm_anchor",
                    expectedGroupId
                );
            }

            function getPermalinkSourcePriority(source = "") {
                if (source === "groups_post_anchor") return 0;
                if (source === "group_permalink_anchor") return 1;
                if (source === "permalink_php_anchor") return 2;
                if (source === "group_query_anchor") return 3;
                if (source === "pcb_anchor") return 4;
                return 5;
            }

            function extractCanonicalPermalinkFromHref(value, expectedGroupId = "") {
                const url = normalizeFacebookUrl(value);
                if (!url) return buildPermalinkDetails("", "");

                const pathname = url.pathname.replace(/\/+$/, "");
                const groupPostMatch = pathname.match(/^\/groups\/([^/?#]+)\/posts?\/(\d+)$/i);
                if (groupPostMatch) {
                    const [, groupId, postId] = groupPostMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        postId,
                        "groups_post_anchor",
                        expectedGroupId
                    );
                }

                const groupPermalinkMatch = pathname.match(/^\/groups\/([^/?#]+)\/permalink\/(\d+)$/i);
                if (groupPermalinkMatch) {
                    const [, groupId, postId] = groupPermalinkMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        postId,
                        "group_permalink_anchor",
                        expectedGroupId
                    );
                }

                const pcbMatch = pathname.match(/^\/groups\/([^/?#]+)\/posts\/pcb\.(\d+)$/i);
                if (pcbMatch) {
                    const [, groupId, postId] = pcbMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        postId,
                        "pcb_anchor",
                        expectedGroupId
                    );
                }

                if (/^\/photo(?:\.php)?$/i.test(pathname)) {
                    return extractPhotoRoutePermalinkDetails(url, expectedGroupId);
                }

                const groupRouteMatch = pathname.match(/^\/groups\/([^/?#]+)(?:\/.*)?$/i);
                if (groupRouteMatch) {
                    const [, groupId] = groupRouteMatch;
                    return buildGroupScopedPermalinkDetails(
                        groupId,
                        extractGroupRouteQueryPostId(url),
                        "group_query_anchor",
                        expectedGroupId
                    );
                }

                if (!/^\/permalink\.php$/i.test(pathname)) return buildPermalinkDetails("", "");
                return buildGroupScopedPermalinkDetails(
                    String(url.searchParams.get("id") || url.searchParams.get("group_id") || expectedGroupId || "").trim(),
                    extractGroupRouteQueryPostId(url),
                    "permalink_php_anchor",
                    expectedGroupId
                );
            }
'''

__all__ = ["PERMALINK_DOM_HELPERS_SCRIPT"]
