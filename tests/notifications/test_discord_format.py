"""Discord notification formatter tests。"""

from __future__ import annotations

from facebook_monitor.notifications.discord_format import build_discord_match_notification_payload
from facebook_monitor.notifications.discord_format import escape_discord_markdown
from facebook_monitor.notifications.payload import MatchNotificationFields


def test_discord_match_payload_uses_content_layout_with_keyword_highlight() -> None:
    """Discord match 使用傳統 content，內容區保留命中高亮。"""

    title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="中信兄弟商品及門票 代購轉售",
            item_kind="post",
            author="陳建宇",
            include_rule="6/3;118",
            text="#售票文 售6/3內野118區25排15到18號有4張連號",
            permalink="https://www.facebook.com/groups/1/posts/2",
        )
    )

    assert title == "Facebook group match"
    assert message.splitlines() == [
        "社團: 中信兄弟商品及門票 代購轉售",
        "類型: 貼文",
        "作者: 陳建宇",
        "命中: 6/3 · 118",
        "",
        "#售票文 售**6/3**內野**118**區25排15到18號有4張連號",
        "",
        "[開啟連結](https://www.facebook.com/groups/1/posts/2)",
        "----------------------------------------",
    ]
    assert "**社團:**" not in message
    assert "**內容:**" not in message
    assert "內容:" not in message
    assert "關鍵字:" not in message
    assert "連結:" not in message


def test_discord_match_payload_preserves_content_newlines() -> None:
    """Discord 內容保留原文換行，方便長內容掃讀。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="測試使用者",
            include_rule="6/5;110;114",
            text=(
                "6/5 110 12排小號 電子票 1080\n"
                "以下位置不含開場舞時間 需自行補票\n"
                "6/6 114 7排890\n"
                "6/7 114 9排790"
            ),
        )
    )

    assert "命中: 6/5 · 110 · 114" in message
    assert (
        "**6/5** **110** 12排小號 電子票 1080\n"
        "以下位置不含開場舞時間 需自行補票\n"
        "6/6 **114** 7排890\n"
        "6/7 **114** 9排790"
    ) in message
    assert "內容:" not in message


def test_discord_match_payload_escapes_content_markdown_around_highlight() -> None:
    """內容區套用命中高亮時，非命中文字與 metadata 仍要 escape Markdown。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試_社團",
            item_kind="post",
            author="A*B",
            include_rule="6/3;118;[票券](evil)",
            text="售6/3_內野118*區 [測試](x)",
        )
    )

    assert "社團: 測試\\_社團" in message
    assert "作者: A\\*B" in message
    assert "命中: 6/3 · 118 · \\[票券\\]\\(evil\\)" in message
    assert (
        "售**6/3**\\_內野**118**\\*區 \\[測試\\]\\(x\\)"
        in message
    )
    assert "內容:" not in message
    assert "[測試](x)" not in message


def test_discord_match_payload_uses_shared_multiline_cleanup() -> None:
    """Discord 內容清理沿用共用多行通知語義，避免通道間重複文字分歧。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="測試使用者",
            include_rule="票券",
            text="票券第一行\n第二行座位\n票券第一行\n第二行座位",
        )
    )

    assert message.count("**票券**第一行") == 1
    assert "**票券**第一行\n第二行座位" in message


def test_escape_discord_markdown_escapes_backslash_first() -> None:
    """反斜線本身也必須 escape，避免吞掉後續 Markdown escape。"""

    assert escape_discord_markdown(r"a\_b") == r"a\\\_b"
