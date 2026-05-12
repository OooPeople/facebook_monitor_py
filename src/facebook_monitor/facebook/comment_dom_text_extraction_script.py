"""Facebook comments DOM script fragment.

職責：保存 `COMMENTS_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

COMMENT_DOM_TEXT_EXTRACTION_SCRIPT = r'''  function findCommentContainerFromPermalinkAnchor(anchor) {
    const candidates = [
      anchor.closest('[role="article"]'),
      anchor.closest('div[aria-label]'),
      anchor.closest('li'),
      anchor.parentElement?.parentElement?.parentElement,
      anchor.parentElement?.parentElement,
    ];
    for (const candidate of candidates) {
      if (candidate instanceof HTMLElement && normalizeText(candidate.innerText || candidate.textContent || "")) {
        return candidate;
      }
    }
    return anchor.closest("div");
  }

  function isLikelyCommentTextNode(text, node) {
    return !getCommentTextNodeRejectionReason(text, node);
  }

  function getCommentTextNodeRejectionReason(text, node) {
    if (!(node instanceof HTMLElement)) return "non_element";
    const normalized = normalizeText(text);
    if (!normalized) return "empty_after_clean";
    if (normalized.length < 2) return "too_short";
    if (nonBodyLabels.has(normalized)) return "non_body_label";
    if (node.closest("a[href]")) return "inside_anchor";
    return "";
  }

  function pushCommentTextDiagnosticSample(diagnostics, sample, limit = 8) {
    const reason = sample.reason || "included";
    diagnostics.reasonCounts[reason] = (diagnostics.reasonCounts[reason] || 0) + 1;
    if (sample.included) {
      diagnostics.includedCount += 1;
    }
    if (diagnostics.samples.length >= limit) return;
    diagnostics.samples.push({
      reason,
      included: Boolean(sample.included),
      text: normalizeText(sample.text).slice(0, 180),
      rawText: normalizeText(sample.rawText).slice(0, 180),
      textLength: normalizeText(sample.text).length,
      rawTextLength: normalizeText(sample.rawText).length,
      tagName: sample.node?.tagName || "",
      role: sample.node?.getAttribute?.("role") || "",
      ariaLabel: normalizeText(sample.node?.getAttribute?.("aria-label") || "").slice(0, 80),
      insideAnchor: Boolean(sample.node?.closest?.("a[href]")),
      isContainedByPreviousSnippet: Boolean(sample.isContainedByPreviousSnippet),
      containsPreviousSnippet: Boolean(sample.containsPreviousSnippet),
      replacedContainedSnippetCount: Number(sample.replacedContainedSnippetCount || 0),
    });
  }

  function extractCommentTextDetails(container) {
    const snippets = [];
    const seen = new Set();
    const diagnostics = {
      candidateCount: 0,
      includedCount: 0,
      reasonCounts: {},
      samples: [],
    };
    for (const node of container.querySelectorAll(commentTextCandidates)) {
      const rawNodeText = normalizeText(node.innerText || node.textContent || "");
      const text = cleanCommentExtractedText(rawNodeText);
      diagnostics.candidateCount += 1;
      const rejectionReason = getCommentTextNodeRejectionReason(text, node);
      if (rejectionReason) {
        pushCommentTextDiagnosticSample(diagnostics, {
          reason: rejectionReason,
          included: false,
          text,
          rawText: rawNodeText,
          node,
        });
        continue;
      }
      const addResult = addTextSnippetWithOverlap(snippets, seen, text);
      pushCommentTextDiagnosticSample(diagnostics, {
        reason: addResult.reason,
        included: addResult.included,
        text,
        rawText: rawNodeText,
        node,
        isContainedByPreviousSnippet: addResult.isContainedByPreviousSnippet,
        containsPreviousSnippet: addResult.containsPreviousSnippet,
        replacedContainedSnippetCount: addResult.replacedContainedSnippetCount,
      });
      if (!addResult.included) continue;
      if (snippets.length >= 6) break;
    }
    if (snippets.length) {
      const rawText = normalizeText(snippets.join(" "));
      const text = cleanCommentExtractedText(rawText);
      diagnostics.finalTextLength = text.length;
      diagnostics.rawTextLength = rawText.length;
      return { text, rawText, source: "comment", textDiagnostics: diagnostics };
    }
    const rawText = normalizeText(container.innerText || container.textContent || "");
    const text = cleanCommentExtractedText(rawText);
    diagnostics.finalTextLength = text.length;
    diagnostics.rawTextLength = rawText.length;
    return { text, rawText, source: "container", textDiagnostics: diagnostics };
  }

'''

__all__ = ["COMMENT_DOM_TEXT_EXTRACTION_SCRIPT"]
