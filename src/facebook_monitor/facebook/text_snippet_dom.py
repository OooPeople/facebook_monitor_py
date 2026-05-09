"""Facebook DOM 文字片段合併 helper。

職責：保存 posts/comments DOM extractor 共用的 snippet overlap 語義，
避免同一段文字因 Facebook 同時輸出完整節點與拆碎子節點而重複。
"""

from __future__ import annotations


TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT = r"""
            const addTextSnippetWithOverlap = (snippets, seenSnippets, value) => {
                const text = normalizeText(value);
                if (!text) {
                    return {
                        included: false,
                        reason: "empty_after_clean",
                        text,
                        isContainedByPreviousSnippet: false,
                        containsPreviousSnippet: false,
                        replacedContainedSnippetCount: 0,
                    };
                }
                if (seenSnippets.has(text)) {
                    return {
                        included: false,
                        reason: "duplicate_snippet",
                        text,
                        isContainedByPreviousSnippet: false,
                        containsPreviousSnippet: false,
                        replacedContainedSnippetCount: 0,
                    };
                }
                const isContainedByPreviousSnippet = snippets.some((snippet) => {
                    return snippet !== text && snippet.includes(text);
                });
                if (isContainedByPreviousSnippet) {
                    return {
                        included: false,
                        reason: "contained_by_existing_snippet",
                        text,
                        isContainedByPreviousSnippet: true,
                        containsPreviousSnippet: false,
                        replacedContainedSnippetCount: 0,
                    };
                }

                let replacedContainedSnippetCount = 0;
                for (let index = snippets.length - 1; index >= 0; index -= 1) {
                    const snippet = snippets[index];
                    if (snippet !== text && text.includes(snippet)) {
                        snippets.splice(index, 1);
                        seenSnippets.delete(snippet);
                        replacedContainedSnippetCount += 1;
                    }
                }
                seenSnippets.add(text);
                snippets.push(text);
                return {
                    included: true,
                    reason: replacedContainedSnippetCount
                        ? "included_replacing_contained_snippets"
                        : "included",
                    text,
                    isContainedByPreviousSnippet: false,
                    containsPreviousSnippet: replacedContainedSnippetCount > 0,
                    replacedContainedSnippetCount,
                };
            };
"""


__all__ = ["TEXT_SNIPPET_OVERLAP_HELPERS_SCRIPT"]
