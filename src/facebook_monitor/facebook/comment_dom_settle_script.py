"""Facebook comments DOM settle script。

職責：保存 comments DOM settle 用的輕量 JavaScript signature，不混入
extractor orchestration，維持 DOM payload 與 Python 收集流程分層。
"""

COMMENT_DOM_SETTLE_SCRIPT = """
(payload) => {
  const limit = Math.max(Number(payload?.limit || 80), 1);
  const anchors = Array.from(document.querySelectorAll(
    'a[href*="comment_id="], a[href*="reply_comment_id="]'
  )).slice(0, limit);
  const signature = anchors.map((anchor) => {
    const href = String(anchor.href || anchor.getAttribute("href") || "");
    const text = String(anchor.textContent || "").replace(/\\s+/g, " ").trim();
    return `${href}#${text}`;
  }).join("|");
  return {
    mode: "comment_dom_settle",
    candidateCount: anchors.length,
    signature,
  };
}
"""

__all__ = ["COMMENT_DOM_SETTLE_SCRIPT"]
