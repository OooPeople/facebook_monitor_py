"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_PERMALINK_SCRIPT = '''            const isLikelyUserProfileHref = (value) => {
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
