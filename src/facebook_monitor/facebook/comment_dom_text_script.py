"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_TEXT_CLEANUP_SCRIPT = r'''

  function cleanCommentExtractedText(value) {
    let text = String(value || "");
    for (const pattern of commentActionTrail) {
      text = text.replace(pattern, " ");
    }
    for (const label of nonBodyLabels) {
      text = text.replaceAll(label, " ");
    }
    return cleanSharedFacebookText(text);
  }

'''

__all__ = ["COMMENT_DOM_TEXT_CLEANUP_SCRIPT"]
