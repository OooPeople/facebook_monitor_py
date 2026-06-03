"""Text newline debug probe tests。"""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from scripts.debug import text_newline_probe
from scripts.debug.text_newline_probe import NewlineProbeOptions
from scripts.debug.text_newline_probe import build_success_result
from scripts.debug.text_newline_probe import classify_playwright_exception
from scripts.debug.text_newline_probe import summarize_probe_payload
from scripts.debug.text_newline_probe import write_result


def test_summarize_probe_payload_counts_inner_text_newline_signal() -> None:
    """newline probe 摘要需分辨 innerText 與結構重組的換行訊號。"""

    summary = summarize_probe_payload(
        {
            "candidates": [
                {
                    "innerTextHasNewline": True,
                    "reconstructedHasNewline": False,
                    "hasLineBreakSignal": True,
                    "lineBreakSignals": {"brCount": 0, "blockBoundaryCount": 0},
                },
                {
                    "innerTextHasNewline": False,
                    "reconstructedHasNewline": True,
                    "hasLineBreakSignal": True,
                    "lineBreakSignals": {"brCount": 1, "blockBoundaryCount": 2},
                },
                {
                    "innerTextHasNewline": False,
                    "reconstructedHasNewline": False,
                    "hasLineBreakSignal": False,
                    "lineBreakSignals": {"brCount": 0, "blockBoundaryCount": 0},
                },
            ]
        }
    )

    assert summary == {
        "candidate_count": 3,
        "newline_candidate_count": 2,
        "inner_text_newline_count": 1,
        "reconstructed_newline_count": 1,
        "structural_signal_count": 1,
    }


def test_build_success_result_marks_newline_not_observed() -> None:
    """有候選但沒有換行訊號時，reason 要保留可行動分類。"""

    result = build_success_result(
        url="https://www.facebook.com/groups/1/posts/2",
        options=NewlineProbeOptions(
            target_url="https://www.facebook.com/groups/1/posts/2",
            output_path=None,
        ),
        payload={
            "candidates": [
                {
                    "innerTextHasNewline": False,
                    "reconstructedHasNewline": False,
                    "hasLineBreakSignal": False,
                    "lineBreakSignals": {"brCount": 0, "blockBoundaryCount": 0},
                }
            ]
        },
    )

    assert result["status"] == "ok"
    assert result["reason"] == "newline_not_observed"
    assert result["summary"]["candidate_count"] == 1


def test_build_success_result_marks_selector_error_failed() -> None:
    """selector 語法錯誤需分類成 selector/extractor 失敗。"""

    result = build_success_result(
        url="https://www.facebook.com/groups/1/posts/2",
        options=NewlineProbeOptions(
            target_url="https://www.facebook.com/groups/1/posts/2",
            mode="selector",
            selector="[",
            output_path=None,
        ),
        payload={
            "candidates": [
                {
                    "kind": "selector_error",
                    "selector": "[",
                    "error": "SyntaxError: Failed to execute 'querySelectorAll'",
                    "hasLineBreakSignal": False,
                }
            ]
        },
    )

    assert result["status"] == "failed"
    assert result["reason"] == "selector_extractor"
    assert result["selector"] == "["
    assert result["summary"]["candidate_count"] == 1
    assert "querySelectorAll" in result["message"]


def test_run_probe_with_result_returns_error_for_selector_extractor(
    monkeypatch: MonkeyPatch,
) -> None:
    """selector/extractor 失敗需反映在 CLI exit code。"""

    def fake_run_probe(_options: NewlineProbeOptions) -> dict[str, object]:
        return {"status": "failed", "reason": "selector_extractor"}

    monkeypatch.setattr(text_newline_probe, "run_probe", fake_run_probe)

    exit_code, result = text_newline_probe.run_probe_with_result(
        NewlineProbeOptions(
            target_url="https://www.facebook.com/groups/1/posts/2",
            output_path=None,
        )
    )

    assert exit_code == 2
    assert result["reason"] == "selector_extractor"


def test_classify_playwright_exception_maps_actionable_reasons() -> None:
    """Playwright 例外分類需對應 probe 失敗語義。"""

    assert classify_playwright_exception(RuntimeError("Timeout 30000ms exceeded")) == "page_load"
    assert classify_playwright_exception(RuntimeError("net::ERR_NAME_NOT_RESOLVED")) == "page_load"
    assert (
        classify_playwright_exception(RuntimeError("user data directory is already in use"))
        == "profile_locked"
    )
    assert classify_playwright_exception(RuntimeError("unexpected")) == "unknown"


def test_write_result_uses_utf8_json(tmp_path: Path) -> None:
    """JSON 輸出需保留中文與換行內容，方便本機檢查。"""

    output_path = tmp_path / "newline.json"
    write_result(
        output_path,
        {
            "status": "ok",
            "payload": {"candidates": [{"innerText": "第一行\n第二行 測試"}]},
        },
    )

    assert "第一行\\n第二行 測試" in output_path.read_text(encoding="utf-8")
