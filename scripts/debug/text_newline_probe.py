"""Debug tool：檢查 Facebook DOM 文字是否能取得換行資訊。

此檔案只做隔離 probe，不呼叫正式 posts/comments scan pipeline，也不寫入 DB。
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.runtime.paths import add_runtime_path_arguments
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths_from_args


DEFAULT_RUNTIME_PATHS = default_runtime_paths()
DEFAULT_PROFILE_DIR = DEFAULT_RUNTIME_PATHS.profile_dir
DEFAULT_OUTPUT_PATH = ROOT / "output" / "debug" / "text_newline_probe.json"

TEXT_NEWLINE_PROBE_SCRIPT = r'''
(payload) => {
  const mode = String(payload?.mode || "auto");
  const selector = String(payload?.selector || "").trim();
  const includePostFallback = Boolean(payload?.includePostFallback);
  const maxCandidates = Math.max(Number(payload?.maxCandidates || 8), 1);
  const maxTextChars = Math.max(Number(payload?.maxTextChars || 600), 80);
  const maxSegments = Math.max(Number(payload?.maxSegments || 60), 10);
  const storyMessageSelector =
    'div[data-ad-comet-preview="message"], div[data-ad-preview="message"], [data-ad-rendering-role="story_message"]';
  const commentPermalinkAnchors = 'a[href*="comment_id="], a[href*="reply_comment_id="]';
  const commentTextCandidates = 'div[dir="auto"], span[dir="auto"]';
  const zeroWidthPattern = /[\u200B-\u200D\uFEFF]/g;

  const normalizeInline = (value) => String(value || "")
    .replace(zeroWidthPattern, "")
    .replace(/[ \t\f\v\r]+/g, " ")
    .trim();
  const meaningfulLines = (value) => String(value || "")
    .replace(zeroWidthPattern, "")
    .split(/\n+/)
    .map((line) => normalizeInline(line))
    .filter(Boolean);
  const truncate = (value) => {
    const text = String(value || "");
    if (text.length <= maxTextChars) return text;
    return `${text.slice(0, maxTextChars)}...`;
  };
  const isVisible = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const isBlockLikeElement = (element) => {
    if (!(element instanceof HTMLElement)) return false;
    const tagName = element.tagName.toLowerCase();
    if (["address", "article", "aside", "blockquote", "dd", "div", "dl", "dt",
      "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
      "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p",
      "pre", "section", "table", "ul"].includes(tagName)) {
      return true;
    }
    const display = window.getComputedStyle(element).display;
    return /^(block|flex|grid|list-item|table)/.test(display);
  };

  const appendSegment = (segments, segment) => {
    if (segments.length >= maxSegments) return;
    segments.push(segment);
  };
  const appendNewline = (segments, source) => {
    const previous = segments[segments.length - 1];
    if (previous?.type === "newline") return;
    appendSegment(segments, { type: "newline", source });
  };
  const appendText = (segments, value, depth) => {
    const text = normalizeInline(value);
    if (!text) return;
    appendSegment(segments, { type: "text", text: truncate(text), depth });
  };

  const collectStructuralSegments = (root) => {
    const segments = [];
    const stats = {
      brCount: 0,
      blockBoundaryCount: 0,
      textNodeCount: 0,
      hiddenElementCount: 0,
    };

    const walk = (node, depth) => {
      if (segments.length >= maxSegments) return;
      if (node.nodeType === Node.TEXT_NODE) {
        stats.textNodeCount += 1;
        appendText(segments, node.nodeValue || "", depth);
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const element = node;
      if (!(element instanceof HTMLElement)) return;
      const tagName = element.tagName.toLowerCase();
      if (["script", "style", "noscript", "svg"].includes(tagName)) return;
      if (tagName === "br") {
        stats.brCount += 1;
        appendNewline(segments, "br");
        return;
      }
      if (!isVisible(element) && element !== root) {
        stats.hiddenElementCount += 1;
        return;
      }

      const hasBoundary = element !== root && isBlockLikeElement(element);
      if (hasBoundary) {
        stats.blockBoundaryCount += 1;
        appendNewline(segments, "block_start");
      }
      for (const child of Array.from(element.childNodes)) {
        walk(child, depth + 1);
      }
      if (hasBoundary) {
        appendNewline(segments, "block_end");
      }
    };

    walk(root, 0);
    return { segments, stats };
  };

  const renderSegments = (segments) => {
    const lines = [""];
    for (const segment of segments) {
      if (segment.type === "newline") {
        if (lines[lines.length - 1].trim()) {
          lines.push("");
        }
        continue;
      }
      if (segment.type !== "text") continue;
      const current = lines[lines.length - 1];
      lines[lines.length - 1] = current
        ? `${current} ${segment.text}`.trim()
        : segment.text;
    }
    return lines.map((line) => normalizeInline(line)).filter(Boolean).join("\n");
  };

  const summarizeElement = (element, kind, selectorLabel, sourceIndex) => {
    const rect = element.getBoundingClientRect();
    const innerText = String(element.innerText || "");
    const textContent = String(element.textContent || "");
    const structural = collectStructuralSegments(element);
    const reconstructedText = renderSegments(structural.segments);
    const innerTextLines = meaningfulLines(innerText);
    const reconstructedLines = meaningfulLines(reconstructedText);
    return {
      kind,
      selector: selectorLabel,
      sourceIndex,
      tagName: element.tagName.toLowerCase(),
      role: element.getAttribute("role") || "",
      ariaLabel: truncate(element.getAttribute("aria-label") || ""),
      viewportTop: Math.round(rect.top),
      viewportHeight: Math.round(rect.height),
      innerText: truncate(innerText),
      textContent: truncate(textContent),
      reconstructedText: truncate(reconstructedText),
      innerTextHasNewline: innerText.includes("\n"),
      innerTextLineCount: innerTextLines.length,
      textContentHasNewline: textContent.includes("\n"),
      reconstructedHasNewline: reconstructedText.includes("\n"),
      reconstructedLineCount: reconstructedLines.length,
      lineBreakSignals: structural.stats,
      segments: structural.segments,
      hasLineBreakSignal: Boolean(
        innerTextLines.length > 1 ||
        reconstructedLines.length > 1 ||
        structural.stats.brCount > 0 ||
        structural.stats.blockBoundaryCount > 0
      ),
    };
  };

  const pushUniqueElement = (items, seen, element, kind, selectorLabel, sourceIndex) => {
    if (!(element instanceof HTMLElement)) return;
    if (seen.has(element)) return;
    if (!isVisible(element)) return;
    const text = normalizeInline(element.innerText || element.textContent || "");
    if (!text) return;
    seen.add(element);
    items.push(summarizeElement(element, kind, selectorLabel, sourceIndex));
  };

  const collectCustomCandidates = () => {
    const items = [];
    const seen = new Set();
    if (!selector) return items;
    let nodes = [];
    try {
      nodes = Array.from(document.querySelectorAll(selector));
    } catch (error) {
      return [{
        kind: "selector_error",
        selector,
        sourceIndex: 0,
        error: String(error),
        hasLineBreakSignal: false,
      }];
    }
    for (const [index, node] of nodes.entries()) {
      pushUniqueElement(items, seen, node, "selector", selector, index);
      if (items.length >= maxCandidates) break;
    }
    return items;
  };

  const collectPostCandidates = () => {
    const items = [];
    const seen = new Set();
    const roots = Array.from(document.querySelectorAll('[role="article"], div[data-pagelet*="FeedUnit"]'));
    for (const [rootIndex, root] of roots.entries()) {
      if (!(root instanceof HTMLElement)) continue;
      const storyNodes = Array.from(root.querySelectorAll(storyMessageSelector));
      if (storyNodes.length) {
        for (const storyNode of storyNodes) {
          pushUniqueElement(items, seen, storyNode, "post", "story_message", rootIndex);
          if (items.length >= maxCandidates) return items;
        }
        continue;
      }
      if (includePostFallback) {
        pushUniqueElement(items, seen, root, "post", "article_fallback", rootIndex);
        if (items.length >= maxCandidates) return items;
      }
    }
    return items;
  };

  const findCommentContainer = (anchor) => {
    return anchor.closest('[role="article"]') ||
      anchor.closest('div[aria-label]') ||
      anchor.closest("li") ||
      anchor.parentElement?.parentElement?.parentElement ||
      anchor.parentElement?.parentElement ||
      anchor.closest("div");
  };

  const collectCommentCandidates = () => {
    const items = [];
    const seen = new Set();
    const anchors = Array.from(document.querySelectorAll(commentPermalinkAnchors));
    for (const [anchorIndex, anchor] of anchors.entries()) {
      if (!(anchor instanceof HTMLAnchorElement)) continue;
      const container = findCommentContainer(anchor);
      if (!(container instanceof HTMLElement)) continue;
      const textNodes = Array.from(container.querySelectorAll(commentTextCandidates));
      const usefulTextNodes = textNodes.filter((node) => {
        if (!(node instanceof HTMLElement)) return false;
        const text = normalizeInline(node.innerText || node.textContent || "");
        return text.length >= 2 && !node.closest("a[href]");
      });
      if (usefulTextNodes.length) {
        for (const textNode of usefulTextNodes.slice(0, 2)) {
          pushUniqueElement(items, seen, textNode, "comment", "comment_text_candidate", anchorIndex);
          if (items.length >= maxCandidates) return items;
        }
      } else {
        pushUniqueElement(items, seen, container, "comment", "comment_container", anchorIndex);
      }
      if (items.length >= maxCandidates) return items;
    }
    return items;
  };

  let candidates = [];
  if (mode === "selector") {
    candidates = collectCustomCandidates();
  } else if (mode === "posts") {
    candidates = collectPostCandidates();
  } else if (mode === "comments") {
    candidates = collectCommentCandidates();
  } else {
    candidates = [...collectPostCandidates(), ...collectCommentCandidates()].slice(0, maxCandidates);
  }

  return {
    mode,
    selector,
    candidateCount: candidates.length,
    newlineCandidateCount: candidates.filter((candidate) => candidate.hasLineBreakSignal).length,
    candidates,
  };
}
'''


class ProbeFailure(RuntimeError):
    """保存 newline probe 的失敗分類。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class NewlineProbeOptions:
    """保存 newline probe 執行選項。"""

    target_url: str
    profile_dir: Path = DEFAULT_PROFILE_DIR
    mode: str = "auto"
    selector: str = ""
    headed: bool = False
    wait_ms: int = 3000
    scroll_rounds: int = 0
    scroll_wait_ms: int = 800
    max_candidates: int = 8
    max_text_chars: int = 600
    include_post_fallback: bool = False
    output_path: Path | None = DEFAULT_OUTPUT_PATH


def parse_args() -> argparse.Namespace:
    """解析 newline probe CLI 參數。"""

    parser = argparse.ArgumentParser(
        description=(
            "Probe whether visible Facebook post/comment DOM text still exposes newline "
            "information. This is a debug-only script and does not run the scanner."
        ),
    )
    add_runtime_path_arguments(parser, include_unsafe_profile_dir=True)
    parser.add_argument("target_url", help="Facebook group feed or post URL to inspect.")
    parser.add_argument(
        "--mode",
        choices=("auto", "posts", "comments", "selector"),
        default="auto",
        help="DOM candidate collection strategy.",
    )
    parser.add_argument(
        "--selector",
        default="",
        help="CSS selector used only with --mode selector.",
    )
    parser.add_argument("--headed", action="store_true", help="Open a visible browser window.")
    parser.add_argument("--wait-ms", type=int, default=3000, help="Wait after navigation.")
    parser.add_argument(
        "--scroll-rounds",
        type=int,
        default=0,
        help="Debug-only viewport scroll rounds before collecting DOM candidates.",
    )
    parser.add_argument(
        "--scroll-wait-ms",
        type=int,
        default=800,
        help="Milliseconds to wait after each debug scroll round.",
    )
    parser.add_argument("--max-candidates", type=int, default=8)
    parser.add_argument("--max-text-chars", type=int, default=600)
    parser.add_argument(
        "--include-post-fallback",
        action="store_true",
        help=(
            "Also inspect whole article fallback nodes when story_message nodes are missing. "
            "This can include UI text and should only be used for selector diagnostics."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="JSON result path. Use --no-output to only print.",
    )
    parser.add_argument("--no-output", action="store_true", help="Do not write a JSON file.")
    args = parser.parse_args()
    paths = resolve_runtime_paths_from_args(args)
    args.profile_dir = paths.profile_dir
    if args.no_output:
        args.output = None
    return args


def options_from_args(args: argparse.Namespace) -> NewlineProbeOptions:
    """將 argparse namespace 轉成明確 options。"""

    return NewlineProbeOptions(
        target_url=args.target_url,
        profile_dir=args.profile_dir,
        mode=args.mode,
        selector=args.selector,
        headed=bool(args.headed),
        wait_ms=max(int(args.wait_ms), 0),
        scroll_rounds=max(int(args.scroll_rounds), 0),
        scroll_wait_ms=max(int(args.scroll_wait_ms), 0),
        max_candidates=max(int(args.max_candidates), 1),
        max_text_chars=max(int(args.max_text_chars), 80),
        include_post_fallback=bool(args.include_post_fallback),
        output_path=args.output,
    )


def classify_playwright_exception(error: Exception) -> str:
    """將 Playwright 例外分類成可行動 reason。"""

    message = str(error).lower()
    if "user data directory is already in use" in message or "processsingleton" in message:
        return "profile_locked"
    if "timeout" in message:
        return "page_load"
    if "net::" in message or "navigation" in message:
        return "page_load"
    return "unknown"


def summarize_probe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """從 DOM payload 產生不依賴 Playwright 的摘要。"""

    candidates = [
        candidate for candidate in payload.get("candidates", []) if isinstance(candidate, dict)
    ]
    newline_candidates = [
        candidate for candidate in candidates if candidate.get("hasLineBreakSignal")
    ]
    inner_text_newline_count = sum(
        1 for candidate in candidates if candidate.get("innerTextHasNewline")
    )
    reconstructed_newline_count = sum(
        1 for candidate in candidates if candidate.get("reconstructedHasNewline")
    )
    structural_signal_count = sum(
        1
        for candidate in candidates
        if (
            (candidate.get("lineBreakSignals") or {}).get("brCount", 0)
            or (candidate.get("lineBreakSignals") or {}).get("blockBoundaryCount", 0)
        )
    )
    return {
        "candidate_count": len(candidates),
        "newline_candidate_count": len(newline_candidates),
        "inner_text_newline_count": inner_text_newline_count,
        "reconstructed_newline_count": reconstructed_newline_count,
        "structural_signal_count": structural_signal_count,
    }


def build_success_result(
    *,
    url: str,
    options: NewlineProbeOptions,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """組合成功 probe 的 JSON 結果。"""

    summary = summarize_probe_payload(payload)
    selector_errors = [
        candidate
        for candidate in payload.get("candidates", [])
        if isinstance(candidate, dict) and candidate.get("kind") == "selector_error"
    ]
    if selector_errors:
        error_message = str(
            selector_errors[0].get("error") or "CSS selector evaluation failed."
        )
        result = build_failure_result("selector_extractor", error_message, url=url)
        result.update(
            {
                "mode": options.mode,
                "selector": options.selector,
                "include_post_fallback": options.include_post_fallback,
                "summary": summary,
                "payload": payload,
            }
        )
        return result
    reason = "ok" if summary["candidate_count"] else "selector_no_candidates"
    if summary["candidate_count"] and not summary["newline_candidate_count"]:
        reason = "newline_not_observed"
    return {
        "status": "ok",
        "reason": reason,
        "url": url,
        "mode": options.mode,
        "selector": options.selector,
        "include_post_fallback": options.include_post_fallback,
        "summary": summary,
        "payload": payload,
    }


def build_failure_result(reason: str, message: str, *, url: str = "") -> dict[str, Any]:
    """組合失敗 probe 的 JSON 結果。"""

    return {
        "status": "failed",
        "reason": reason,
        "message": message,
        "url": url,
    }


def write_result(path: Path | None, result: dict[str, Any]) -> None:
    """依需要寫出 JSON probe 結果。"""

    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def print_result(result: dict[str, Any]) -> None:
    """輸出給 CLI 使用者閱讀的 JSON 結果。"""

    print(json.dumps(result, ensure_ascii=True, indent=2))


def _perform_debug_scrolls(page: Any, *, rounds: int, wait_ms: int) -> None:
    """執行 debug-only scroll，協助 feed 載入可見候選節點。"""

    for _index in range(max(rounds, 0)):
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.85, 600));")
        page.wait_for_timeout(wait_ms)


def run_probe(options: NewlineProbeOptions) -> dict[str, Any]:
    """執行一次 Facebook DOM newline probe。"""

    if options.mode == "selector" and not options.selector.strip():
        raise ProbeFailure("selector_required", "--selector is required when --mode selector.")
    if not options.profile_dir.exists():
        raise ProbeFailure(
            "login_session",
            "Profile does not exist. Run facebook-monitor-login first: "
            f"{options.profile_dir}",
        )

    with acquire_profile_lease(options.profile_dir, "debug text newline probe"):
        with sync_playwright() as playwright:
            context = launch_persistent_context_sync(
                playwright,
                BrowserRuntimeOptions(
                    profile_dir=options.profile_dir,
                    headless=not options.headed,
                ),
            )
            try:
                page = context.new_page()
                page.goto(options.target_url, wait_until="domcontentloaded")
                page.wait_for_timeout(options.wait_ms)
                _perform_debug_scrolls(
                    page,
                    rounds=options.scroll_rounds,
                    wait_ms=options.scroll_wait_ms,
                )
                body_text = page.locator("body").inner_text(timeout=10000)
                body_text_lower = body_text.lower()
                if "log into facebook" in body_text_lower or "登入 facebook" in body_text_lower:
                    raise ProbeFailure("login_session", "Facebook login is required.")
                if (
                    "目前無法查看此內容" in body_text
                    or "this content isn't available" in body_text_lower
                    or "this content is not available" in body_text_lower
                ):
                    raise ProbeFailure("page_load", "Facebook content is unavailable.")
                payload = page.evaluate(
                    TEXT_NEWLINE_PROBE_SCRIPT,
                    {
                        "mode": options.mode,
                        "selector": options.selector,
                        "maxCandidates": options.max_candidates,
                        "maxTextChars": options.max_text_chars,
                        "includePostFallback": options.include_post_fallback,
                    },
                )
                if not isinstance(payload, dict):
                    raise ProbeFailure("headless_dom", "Probe returned an unexpected payload.")
                return build_success_result(url=page.url, options=options, payload=payload)
            finally:
                context.close()


def run_probe_with_result(options: NewlineProbeOptions) -> tuple[int, dict[str, Any]]:
    """執行 probe 並將例外轉為穩定 result。"""

    try:
        result = run_probe(options)
        if result.get("status") == "failed":
            return 2, result
        return 0 if result.get("reason") != "selector_no_candidates" else 2, result
    except ProbeFailure as error:
        return 2, build_failure_result(error.reason, str(error), url=options.target_url)
    except ProfileLeaseError as error:
        return 2, build_failure_result("profile_locked", str(error), url=options.target_url)
    except (PlaywrightTimeoutError, PlaywrightError) as error:
        reason = classify_playwright_exception(error)
        return 2, build_failure_result(reason, str(error), url=options.target_url)
    except Exception as error:
        return 2, build_failure_result("unknown", str(error), url=options.target_url)


def main() -> int:
    """CLI entrypoint：解析參數後執行 newline probe。"""

    options = options_from_args(parse_args())
    exit_code, result = run_probe_with_result(options)
    write_result(options.output_path, result)
    print_result(result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
