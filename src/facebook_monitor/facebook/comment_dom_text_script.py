"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_TEXT_CLEANUP_SCRIPT = r'''

  function cleanCommentExtractedText(value) {
    let text = normalizeText(value);
    for (const pattern of commentActionTrail) {
      text = text.replace(pattern, " ");
    }
    for (const label of nonBodyLabels) {
      text = text.replaceAll(label, " ");
    }
    return collapseRepeatedAdjacentText(text);
  }

  function collapseRepeatedAdjacentText(value) {
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
  }

'''

__all__ = ["COMMENT_DOM_TEXT_CLEANUP_SCRIPT"]
