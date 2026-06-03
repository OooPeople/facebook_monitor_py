"""Facebook DOM 文字清理共用 JavaScript 片段。

職責：保存 posts/comments DOM extractor 共用的文字正規化、展開/收合
UI label 清理與重複文字折疊 helper，避免兩套 extractor 各自複製規則。
"""

from __future__ import annotations

import json

from facebook_monitor.facebook.text_cleanup import FACEBOOK_COLLAPSE_LESS_LABELS
from facebook_monitor.facebook.text_cleanup import FACEBOOK_EXPAND_MORE_LABELS


def _js_array(values: tuple[str, ...]) -> str:
    """將 Python label 常數輸出成 JavaScript array literal。"""

    return json.dumps(values, ensure_ascii=False)


TEXT_CLEANUP_HELPERS_SCRIPT = r'''
            const facebookExpandMoreLabels = __FACEBOOK_EXPAND_MORE_LABELS__;
            const facebookCollapseLessLabels = __FACEBOOK_COLLAPSE_LESS_LABELS__;
            const facebookExpandCollapseLabels = [
                ...facebookExpandMoreLabels,
                ...facebookCollapseLessLabels,
            ];

            function normalizeText(value) {
                return String(value || "")
                    .replace(/[\u200B-\u200D\uFEFF]/g, "")
                    .replace(/\s+/g, " ")
                    .trim();
            }

            function normalizeMultilineText(value) {
                const lines = String(value || "")
                    .replace(/[\u200B-\u200D\uFEFF]/g, "")
                    .replace(/\r\n/g, "\n")
                    .replace(/\r/g, "\n")
                    .split("\n")
                    .map((line) => line.replace(/\s+/g, " ").trim());
                while (lines.length && !lines[0]) lines.shift();
                while (lines.length && !lines[lines.length - 1]) lines.pop();

                const compactedLines = [];
                let previousWasEmpty = false;
                for (const line of lines) {
                    if (!line) {
                        if (previousWasEmpty) continue;
                        previousWasEmpty = true;
                        compactedLines.push("");
                        continue;
                    }
                    previousWasEmpty = false;
                    compactedLines.push(line);
                }
                return compactedLines.join("\n");
            }

            function isFacebookExpandMoreLabelText(value) {
                return facebookExpandMoreLabels.includes(normalizeText(value));
            }

            function isFacebookExpandCollapseLabelText(value) {
                return facebookExpandCollapseLabels.includes(normalizeText(value));
            }

            function stripFacebookExpandCollapseLabels(value) {
                let text = normalizeText(value);
                if (!text) return "";
                while (text) {
                    const originalText = text;
                    for (const label of facebookExpandCollapseLabels) {
                        if (text === label) return "";
                        if (text.endsWith(` ${label}`)) {
                            text = normalizeText(text.slice(0, -label.length));
                        }
                    }
                    if (text === originalText) break;
                }
                return normalizeText(text);
            }

            function stripFacebookExpandCollapseLabelsMultiline(value) {
                const text = normalizeMultilineText(value);
                if (!text) return "";
                const lines = text
                    .split("\n")
                    .map((line) => stripFacebookExpandCollapseLabels(line));
                while (lines.length && !lines[0]) lines.shift();
                while (lines.length && !lines[lines.length - 1]) lines.pop();
                return normalizeMultilineText(lines.join("\n"));
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

            function collapseRepeatedAdjacentMultilineText(value) {
                let text = normalizeMultilineText(value);
                if (!text) return "";
                if (!text.includes("\n")) {
                    return collapseRepeatedAdjacentText(text);
                }
                text = normalizeMultilineText(
                    text
                        .split("\n")
                        .map((line) => collapseRepeatedAdjacentText(line))
                        .join("\n")
                );
                while (true) {
                    const lines = text.split("\n");
                    if (lines.length > 1 && lines.length % 2 === 0) {
                        const halfLength = lines.length / 2;
                        const leftLines = lines.slice(0, halfLength);
                        const rightLines = lines.slice(halfLength);
                        const left = leftLines.join("\n");
                        if (
                            left.length >= 8 &&
                            leftLines.length === rightLines.length &&
                            leftLines.every((line, index) => line === rightLines[index])
                        ) {
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

            function cleanSharedFacebookText(value) {
                return collapseRepeatedAdjacentText(
                    stripFacebookExpandCollapseLabels(collapseRepeatedAdjacentText(value))
                );
            }

            function cleanSharedFacebookMultilineText(value) {
                return collapseRepeatedAdjacentMultilineText(
                    stripFacebookExpandCollapseLabelsMultiline(
                        collapseRepeatedAdjacentMultilineText(value)
                    )
                );
            }
'''.replace(
    "__FACEBOOK_EXPAND_MORE_LABELS__",
    _js_array(FACEBOOK_EXPAND_MORE_LABELS),
).replace(
    "__FACEBOOK_COLLAPSE_LESS_LABELS__",
    _js_array(FACEBOOK_COLLAPSE_LESS_LABELS),
)

__all__ = ["TEXT_CLEANUP_HELPERS_SCRIPT"]
