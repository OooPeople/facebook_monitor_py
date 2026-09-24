"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

from facebook_monitor.facebook.feed_dom_selectors import FEED_ROOT_SELECTORS_SCRIPT
from facebook_monitor.facebook.feed_dom_selectors import (
    POST_CONTAINER_CANDIDATE_SELECTORS_SCRIPT,
)
from facebook_monitor.facebook.feed_dom_selectors import POST_PERMALINK_ANCHOR_SELECTOR

FEED_DOM_BOOTSTRAP_SCRIPT = (
    "async (maxItems) => {\n"
    f"            const feedRoots = {FEED_ROOT_SELECTORS_SCRIPT};\n"
    "            const postContainerCandidates = "
    f"{POST_CONTAINER_CANDIDATE_SELECTORS_SCRIPT};\n"
    f"            const postPermalinkAnchors = {POST_PERMALINK_ANCHOR_SELECTOR!r};\n"
    """
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
"""
)

__all__ = ["FEED_DOM_BOOTSTRAP_SCRIPT"]
