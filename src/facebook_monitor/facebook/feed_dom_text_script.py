"""Facebook feed DOM script fragment.

職責：保存 `POST_LIKE_ITEMS_SCRIPT` 的單一責任 JavaScript 片段。
"""

FEED_DOM_TEXT_SCRIPT = '''
            const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
            const isVisible = (element) => {
                if (!(element instanceof HTMLElement)) return false;
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };
            const isCommentPermalinkHref = (href) =>
                /[?&](?:comment_id|reply_comment_id)=/i.test(String(href || ""));
            const isPostPermalinkHref = (href) => {
                const value = String(href || "");
                return !isCommentPermalinkHref(value) && (
                    value.includes('/posts/') ||
                    value.includes('/post/') ||
                    value.includes('/permalink/') ||
                    value.includes('multi_permalinks=') ||
                    value.includes('story_fbid=') ||
                    value.includes('set=gm.')
                );
            };
            const hasCommentActionTrail = (value) => {
                const text = normalizeText(value);
                return Boolean(text && commentActionTrail.some((pattern) => pattern.test(text)));
            };
            const stripCommentActionTrail = (value) => {
                let text = String(value || "");
                if (!text) return "";
                for (const pattern of commentActionTrail) {
                    text = text.replace(pattern, " ");
                }
                return normalizeText(text);
            };
            const cleanExtractedText = (value) => {
                let text = stripCommentActionTrail(value);
                if (!text) return "";
                for (const fragment of noisyTextFragments) {
                    text = text.replaceAll(fragment, " ");
                }
                for (const pattern of cleanedTextNoise) {
                    text = text.replace(pattern, " ");
                }
                return cleanSharedFacebookText(text);
            };
            const cleanExtractedDisplayText = (value) => {
                let text = String(value || "");
                for (const pattern of commentActionTrail) {
                    text = text.replace(pattern, "\\n");
                }
                for (const fragment of noisyTextFragments) {
                    text = text.replaceAll(fragment, "\\n");
                }
                for (const pattern of cleanedTextNoise) {
                    text = text.replace(pattern, "\\n");
                }
                return cleanSharedFacebookMultilineText(text);
            };
            const isFeedSortControlText = (value) =>
                normalizeText(value).includes("社團動態消息排序方式");
            const getElementText = (element) => normalizeText(
                element?.innerText ||
                element?.textContent ||
                element?.getAttribute?.("aria-label") ||
                ""
            );
            const sortElementsByViewportTop = (elements) => {
                return [...elements].sort((left, right) => {
                    return Math.round(left.getBoundingClientRect().top) -
                        Math.round(right.getBoundingClientRect().top);
                });
            };
            const collectUniqueTextSnippets = (container, selectors, minLength, maxItems, upperOnly) => {
                const snippets = [];
                const seen = new Set();
                const displayTextBySnippet = new Map();
                const containerRect = container.getBoundingClientRect();
                for (const selector of selectors) {
                    for (const node of Array.from(container.querySelectorAll(selector))) {
                        if (!(node instanceof HTMLElement)) continue;
                        if (upperOnly) {
                            const rect = node.getBoundingClientRect();
                            const relativeTop = rect.top - containerRect.top;
                            const upperLimit = Math.max(210, containerRect.height * 0.46);
                            if (relativeTop > upperLimit) continue;
                        }
                        const rawNodeText = node.innerText || node.textContent || "";
                        const text = cleanExtractedText(rawNodeText);
                        if (text.length < minLength) continue;
                        const displayText = cleanExtractedDisplayText(rawNodeText);
                        const addResult = addTextSnippetWithOverlap(snippets, seen, text);
                        if (!addResult.included) continue;
                        for (const replacedText of addResult.replacedContainedSnippetValues || []) {
                            displayTextBySnippet.delete(replacedText);
                        }
                        displayTextBySnippet.set(text, displayText || text);
                        if (snippets.length >= maxItems) {
                            return snippets.map((snippet) => ({
                                text: snippet,
                                displayText: displayTextBySnippet.get(snippet) || snippet,
                            }));
                        }
                    }
                }
                return snippets.map((snippet) => ({
                    text: snippet,
                    displayText: displayTextBySnippet.get(snippet) || snippet,
                }));
            };
            const extractPostTextDetails = (container) => {
                const primarySnippets = collectUniqueTextSnippets(
                    container,
                    [postStoryMessage],
                    2,
                    8,
                    false
                );
                if (primarySnippets.length) {
                    const rawText = normalizeText(primarySnippets.map((snippet) => snippet.text).join(" "));
                    const rawDisplayText = normalizeMultilineText(
                        primarySnippets.map((snippet) => snippet.displayText || snippet.text).join("\\n")
                    );
                    const text = cleanExtractedText(rawText);
                    const displayText = cleanExtractedDisplayText(rawDisplayText) || text;
                    return { text, rawText, displayText, rawDisplayText, source: "primary" };
                }

                const fallbackSnippets = collectUniqueTextSnippets(
                    container,
                    ['div[dir="auto"]', 'span[dir="auto"]'],
                    6,
                    8,
                    true
                );
                if (fallbackSnippets.length) {
                    const rawText = normalizeText(fallbackSnippets.map((snippet) => snippet.text).join(" "));
                    const rawDisplayText = normalizeMultilineText(
                        fallbackSnippets.map((snippet) => snippet.displayText || snippet.text).join("\\n")
                    );
                    const text = cleanExtractedText(rawText);
                    const displayText = cleanExtractedDisplayText(rawDisplayText) || text;
                    return { text, rawText, displayText, rawDisplayText, source: "fallback" };
                }

                const rawText = normalizeText(container.innerText || "");
                const rawDisplayText = normalizeMultilineText(container.innerText || container.textContent || "");
                const text = cleanExtractedText(rawText);
                const displayText = cleanExtractedDisplayText(rawDisplayText) || text;
                return { text, rawText, displayText, rawDisplayText, source: "container" };
            };
            const extractAuthor = (node) => {
                for (const selector of authorSelectors) {
                    const candidates = Array.from(node.querySelectorAll(selector));
                    for (const candidate of candidates) {
                        const text = (candidate.innerText || "").replace(/\\s*[·•]\\s*追蹤\\s*$/u, "").trim();
                        if (!text || text.length > 80 || authorUiLabels.test(text)) {
                            continue;
                        }
                        return text;
                    }
                }
                return "";
            };
            const isPostTextExpander = (element, container) => {
                if (!(element instanceof HTMLElement) || !(container instanceof HTMLElement)) return false;
                if (!isVisible(element)) return false;
                const text = getElementText(element);
                if (!isFacebookExpandMoreLabelText(text)) return false;
                const containerRect = container.getBoundingClientRect();
                const elementRect = element.getBoundingClientRect();
                const relativeTop = elementRect.top - containerRect.top;
                const upperRegionThreshold = Math.max(220, Math.round(containerRect.height * 0.72));
                return relativeTop >= -12 && relativeTop <= upperRegionThreshold;
            };
            const findPostTextExpanders = (container) => {
                if (!(container instanceof HTMLElement)) return [];
                const results = [];
                const seen = new Set();
                for (const selector of ['div[role="button"]', 'span[role="button"]', 'a[role="button"]', "button"]) {
                    for (const node of container.querySelectorAll(selector)) {
                        if (!isPostTextExpander(node, container)) continue;
                        if (seen.has(node)) continue;
                        seen.add(node);
                        results.push(node);
                    }
                }
                return sortElementsByViewportTop(results);
            };
            const expandCollapsedPostText = async (container) => {
                if (!(container instanceof HTMLElement)) {
                    return { expandAttempted: false, expandCount: 0 };
                }
                let expandCount = 0;
                for (let attempt = 0; attempt < 2; attempt += 1) {
                    const expanders = findPostTextExpanders(container);
                    if (!expanders.length) break;
                    expanders[0].click();
                    expandCount += 1;
                    await sleep(220);
                }
                return {
                    expandAttempted: expandCount > 0,
                    expandCount,
                };
            };
'''

__all__ = ["FEED_DOM_TEXT_SCRIPT"]
