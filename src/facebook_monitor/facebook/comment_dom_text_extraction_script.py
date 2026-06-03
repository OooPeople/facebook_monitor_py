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

  function isLikelyCommentTextNode(text, node, author = "") {
    return !getCommentTextNodeRejectionReason(text, node, author);
  }

  function getCommentTextNodeRejectionReason(text, node, author = "") {
    if (!(node instanceof HTMLElement)) return "non_element";
    const normalized = normalizeText(text);
    if (!normalized) return "empty_after_clean";
    if (normalized.length < 2) return "too_short";
    if (nonBodyLabels.has(normalized)) return "non_body_label";
    if (isFacebookExpandCollapseLabelText(normalized)) return "non_body_label";
    if (node.closest("a[href]")) return "inside_anchor";
    if (author && normalized === normalizeText(author)) return "author_label";
    if (author && containsAuthorLinkWithNestedBodyCandidate(node, author)) {
      return "contains_author_link";
    }
    return "";
  }

  function isAuthorAnchorForText(anchor, author) {
    if (!(anchor instanceof HTMLAnchorElement)) return false;
    const text = normalizeText(anchor.innerText || anchor.textContent || "");
    if (!author || text !== normalizeText(author)) return false;
    return isLikelyCommentAuthorHref(anchor.href || anchor.getAttribute("href") || "");
  }

  function containsAuthorLinkWithNestedBodyCandidate(node, author) {
    if (!(node instanceof HTMLElement)) return false;
    const authorAnchors = Array.from(node.querySelectorAll('a[role="link"], a[href]'))
      .filter((anchor) => isAuthorAnchorForText(anchor, author));
    if (!authorAnchors.length) return false;
    for (const candidate of node.querySelectorAll(commentTextCandidates)) {
      if (!(candidate instanceof HTMLElement)) continue;
      if (candidate === node) continue;
      if (authorAnchors.some((anchor) => anchor.contains(candidate))) continue;
      if (candidate.closest("a[href]")) continue;
      const candidateText = cleanCommentExtractedText(
        normalizeText(candidate.innerText || candidate.textContent || "")
      );
      if (
        candidateText &&
        candidateText.length >= 2 &&
        candidateText !== normalizeText(author) &&
        !nonBodyLabels.has(candidateText) &&
        !isFacebookExpandCollapseLabelText(candidateText)
      ) {
        return true;
      }
    }
    return false;
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

  function extractCommentTextDetails(container, author = "") {
    const snippets = [];
    const seen = new Set();
    const displayTextBySnippet = new Map();
    const authorText = normalizeText(author);
    const diagnostics = {
      candidateCount: 0,
      includedCount: 0,
      reasonCounts: {},
      samples: [],
    };
    for (const node of container.querySelectorAll(commentTextCandidates)) {
      const rawNodeValue = node.innerText || node.textContent || "";
      const rawNodeText = normalizeText(rawNodeValue);
      const text = cleanCommentExtractedText(rawNodeText);
      const displayText = cleanCommentExtractedDisplayText(rawNodeValue);
      diagnostics.candidateCount += 1;
      const rejectionReason = getCommentTextNodeRejectionReason(text, node, authorText);
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
      for (const replacedText of addResult.replacedContainedSnippetValues || []) {
        displayTextBySnippet.delete(replacedText);
      }
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
      displayTextBySnippet.set(text, displayText || text);
      if (snippets.length >= 6) break;
    }
    if (snippets.length) {
      const rawText = normalizeText(snippets.join(" "));
      const rawDisplayText = normalizeMultilineText(
        snippets.map((snippet) => displayTextBySnippet.get(snippet) || snippet).join("\n")
      );
      const text = cleanCommentExtractedText(rawText);
      const displayText = cleanCommentExtractedDisplayText(rawDisplayText) || text;
      diagnostics.finalTextLength = text.length;
      diagnostics.rawTextLength = rawText.length;
      diagnostics.displayTextLineCount = displayText ? displayText.split("\n").length : 0;
      diagnostics.rawDisplayTextLength = rawDisplayText.length;
      return {
        text,
        rawText,
        displayText,
        rawDisplayText,
        source: "comment",
        textDiagnostics: diagnostics,
      };
    }
    const rawText = normalizeText(container.innerText || container.textContent || "");
    const rawDisplayText = normalizeMultilineText(container.innerText || container.textContent || "");
    const text = cleanCommentExtractedText(rawText);
    const displayText = cleanCommentExtractedDisplayText(rawDisplayText) || text;
    diagnostics.finalTextLength = text.length;
    diagnostics.rawTextLength = rawText.length;
    diagnostics.displayTextLineCount = displayText ? displayText.split("\n").length : 0;
    diagnostics.rawDisplayTextLength = rawDisplayText.length;
    return { text, rawText, displayText, rawDisplayText, source: "container", textDiagnostics: diagnostics };
  }

'''

__all__ = ["COMMENT_DOM_TEXT_EXTRACTION_SCRIPT"]
