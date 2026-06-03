"""Facebook 文字清理測試。"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from facebook_monitor.facebook.text_cleanup import clean_facebook_text
from facebook_monitor.facebook.text_cleanup import clean_facebook_multiline_text
from facebook_monitor.facebook.text_cleanup import strip_facebook_expand_collapse_labels
from facebook_monitor.facebook.text_cleanup_dom import TEXT_CLEANUP_HELPERS_SCRIPT


def _run_dom_strip_cases(values: list[str]) -> list[str]:
    """用 Node 執行 DOM 共用清理片段，確認 Python/JS 邏輯沒有漂移。"""

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is required for DOM cleanup behavior tests")
    script = f"""
const helpers = (() => {{
{TEXT_CLEANUP_HELPERS_SCRIPT}
    return {{ stripFacebookExpandCollapseLabels, cleanSharedFacebookMultilineText }};
}})();
const values = {json.dumps(values, ensure_ascii=False)};
console.log(JSON.stringify(values.map((value) => helpers.stripFacebookExpandCollapseLabels(value))));
"""
    result = subprocess.run(
        [node_bin, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    return json.loads(result.stdout)


def _run_dom_multiline_cleanup_cases(values: list[str]) -> list[str]:
    """用 Node 執行 DOM 顯示用清理片段，確認 Python/JS 邏輯沒有漂移。"""

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is required for DOM cleanup behavior tests")
    script = f"""
const helpers = (() => {{
{TEXT_CLEANUP_HELPERS_SCRIPT}
    return {{ cleanSharedFacebookMultilineText }};
}})();
const values = {json.dumps(values, ensure_ascii=False)};
console.log(JSON.stringify(values.map((value) => helpers.cleanSharedFacebookMultilineText(value))));
"""
    result = subprocess.run(
        [node_bin, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    return json.loads(result.stdout)


def test_strip_facebook_expand_collapse_labels_removes_expand_and_less_ui_text() -> None:
    """展開/收合按鈕 label 不應留在掃描內容中。"""

    assert strip_facebook_expand_collapse_labels(
        "#售 5/29 116-31排 4連號 原價1200售 13-16號 顯示較少"
    ) == "#售 5/29 116-31排 4連號 原價1200售 13-16號"
    assert strip_facebook_expand_collapse_labels("內容 顯示更少 See less") == "內容"
    assert strip_facebook_expand_collapse_labels("內容 顯示更多 查看更多 See more") == "內容"
    assert strip_facebook_expand_collapse_labels("內容 顯示\u200b較少") == "內容"
    assert strip_facebook_expand_collapse_labels("內容 顯示\u200b更多 See\u200b more") == "內容"


def test_strip_facebook_expand_collapse_labels_keeps_real_content() -> None:
    """清理 UI label 時不可誤刪正常貼文或留言內容。"""

    assert strip_facebook_expand_collapse_labels("顯示更多資訊請看留言") == "顯示更多資訊請看留言"
    assert strip_facebook_expand_collapse_labels("See more details in comments") == (
        "See more details in comments"
    )
    assert strip_facebook_expand_collapse_labels("內容 顯示更多 詳細說明") == (
        "內容 顯示更多 詳細說明"
    )


def test_dom_strip_facebook_expand_collapse_labels_matches_python_edges() -> None:
    """DOM 共用片段需和 Python 共用清理保留同樣的尾端 label 語義。"""

    values = [
        "內容 顯示較少",
        "內容 顯示更多 查看更多 See more",
        "顯示更多資訊請看留言",
        "See more details in comments",
        "內容 顯示更多 詳細說明",
        "內容 顯示\u200b較少",
        "內容 顯示\u200b更多 See\u200b more",
    ]

    assert _run_dom_strip_cases(values) == [
        strip_facebook_expand_collapse_labels(value) for value in values
    ]


def test_clean_facebook_text_keeps_repeated_text_cleanup_after_label_removal() -> None:
    """共用清理需同時保留既有重複文字折疊語義。"""

    assert clean_facebook_text(
        "這是一則有票券關鍵字的留言 顯示較少 這是一則有票券關鍵字的留言 顯示較少"
    ) == "這是一則有票券關鍵字的留言"


def test_clean_facebook_multiline_text_preserves_lines_and_removes_ui_labels() -> None:
    """顯示用清理保留內容換行，但仍移除 Facebook UI label。"""

    assert clean_facebook_multiline_text(
        "第一行  票券\r\n第二行 顯示更多\n\n第三行"
    ) == "第一行 票券\n第二行\n\n第三行"


def test_clean_facebook_multiline_text_collapses_repeated_blocks() -> None:
    """顯示用清理仍需折疊 Facebook DOM 造成的整段重複。"""

    assert clean_facebook_multiline_text(
        "第一行票券\n第二行說明\n第一行票券\n第二行說明"
    ) == "第一行票券\n第二行說明"


def test_dom_clean_facebook_multiline_text_matches_python_edges() -> None:
    """DOM 顯示用清理需和 Python 共用清理保留同樣換行語義。"""

    values = [
        "第一行  票券\r\n第二行 顯示更多\n\n第三行",
        "第一行票券\n第二行說明\n第一行票券\n第二行說明",
    ]

    assert _run_dom_multiline_cleanup_cases(values) == [
        clean_facebook_multiline_text(value) for value in values
    ]
