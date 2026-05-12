"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_BOOTSTRAP_SCRIPT = r'''(payload) => {
  const limit = Math.max(Number(payload?.limit || 5), 1);
  const scanTarget = {
    groupId: String(payload?.groupId || ""),
    parentPostId: String(payload?.parentPostId || ""),
  };
  const commentPermalinkAnchors = 'a[href*="comment_id="], a[href*="reply_comment_id="]';
  const commentTextCandidates = 'div[dir="auto"], span[dir="auto"]';
  const commentActionTrail = [
    /(?:^|\s)(讚|回覆|分享|檢舉|隱藏|Like|Reply|Share)(?:\s|$)/gi,
    /\b(?:\d+\s*)?(?:分鐘|小時|天|週|月|年|m|h|d|w|mo|y)\b/gi,
  ];
  const nonBodyLabels = new Set([
    "讚",
    "回覆",
    "分享",
    "編輯紀錄",
    "查看更多",
    "顯示更多",
    "Like",
    "Reply",
    "Share",
    "See more",
  ]);

  function normalizeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

'''

__all__ = ["COMMENT_DOM_BOOTSTRAP_SCRIPT"]
