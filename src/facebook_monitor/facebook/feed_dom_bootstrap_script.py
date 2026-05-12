"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_BOOTSTRAP_SCRIPT = '''async (maxItems) => {
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
'''

__all__ = ["FEED_DOM_BOOTSTRAP_SCRIPT"]
