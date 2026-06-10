"""Text newline debug probe DOM contract tests。"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from scripts.debug.text_newline_probe import TEXT_NEWLINE_PROBE_SCRIPT


def test_text_newline_probe_detects_post_br_and_block_boundaries() -> None:
    """newline probe 應能在貼文文字節點辨識 `<br>` 與 block-like 換行。"""

    payload = _evaluate_probe(
        """
        <main>
          <div role="article">
            <a href="/groups/222518561920110/posts/9999999999999999">時間</a>
            <div data-ad-comet-preview="message">第一行<br>第二行<div>第三行</div><span>第四行</span></div>
          </div>
        </main>
        """,
        {"mode": "posts", "maxCandidates": 3, "maxTextChars": 200},
    )

    candidate = payload["candidates"][0]

    assert payload["candidateCount"] == 1
    assert payload["newlineCandidateCount"] == 1
    assert candidate["kind"] == "post"
    assert candidate["innerTextHasNewline"] is True
    assert candidate["textContentHasNewline"] is False
    assert candidate["reconstructedHasNewline"] is True
    assert candidate["lineBreakSignals"]["brCount"] == 1
    assert candidate["lineBreakSignals"]["blockBoundaryCount"] >= 1


def test_text_newline_probe_detects_comment_text_candidate_linebreaks() -> None:
    """newline probe 應能在留言文字候選節點辨識換行訊號。"""

    payload = _evaluate_probe(
        """
        <main>
          <div role="article" aria-label="留言">
            <div dir="auto">留言第一行<br>留言第二行</div>
            <a href="/groups/222518561920110/posts/9999999999999999?comment_id=888">
              1 分鐘
            </a>
          </div>
        </main>
        """,
        {"mode": "comments", "maxCandidates": 3, "maxTextChars": 200},
    )

    candidate = payload["candidates"][0]

    assert payload["candidateCount"] == 1
    assert payload["newlineCandidateCount"] == 1
    assert candidate["kind"] == "comment"
    assert candidate["selector"] == "comment_text_candidate"
    assert candidate["innerTextLineCount"] == 2
    assert candidate["lineBreakSignals"]["brCount"] == 1


def _evaluate_probe(html: str, payload: dict[str, Any]) -> dict[str, Any]:
    """用 Playwright Chromium 執行 newline probe 的 DOM payload。"""

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, timeout=10_000)
        except PlaywrightError as exc:
            pytest.skip(f"chromium browser is not installed: {exc}")
        try:
            page = browser.new_page()
            page.set_content(html)
            result = page.evaluate(TEXT_NEWLINE_PROBE_SCRIPT, payload)
        finally:
            browser.close()
    if not isinstance(result, dict):
        raise AssertionError("newline probe did not return an object payload")
    return result
