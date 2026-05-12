"""Web UI keyword highlight helper tests。"""

from __future__ import annotations

from facebook_monitor.webapp.highlight import build_highlight_segments


def test_highlight_segments_keep_original_nfkc_text() -> None:
    """半形 keyword 命中全形原文時，高亮仍保留原文片段。"""

    segments = build_highlight_segments("售 ６／６ 內野票", "6/6")

    assert [(segment.text, segment.highlighted) for segment in segments] == [
        ("售 ", False),
        ("６／６", True),
        (" 內野票", False),
    ]


def test_highlight_segments_use_text_segments_for_untrusted_text() -> None:
    """外部內容只切 segment，不產生 HTML 字串。"""

    segments = build_highlight_segments("<script>票券</script>", "票券")

    assert [(segment.text, segment.highlighted) for segment in segments] == [
        ("<script>", False),
        ("票券", True),
        ("</script>", False),
    ]


def test_highlight_segments_mark_all_and_terms() -> None:
    """AND rule 的每個 term 都會被標示，方便使用者辨識命中原因。"""

    segments = build_highlight_segments("4/4 想找熱區兩張", "4/4 熱區")

    assert [(segment.text, segment.highlighted) for segment in segments] == [
        ("4/4", True),
        (" 想找", False),
        ("熱區", True),
        ("兩張", False),
    ]
