"""Discord notification formatter tests。"""

from __future__ import annotations

from facebook_monitor.notifications.discord_format import build_discord_match_notification_payload
from facebook_monitor.notifications.discord_format import escape_discord_markdown
from facebook_monitor.notifications.payload import MatchNotificationFields


def test_discord_match_payload_highlights_content_keywords_only() -> None:
    """Discord match 只在內容原文命中處加粗底線，關鍵字欄位維持純文字。"""

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
    assert "關鍵字: 6/3;118" in message
    assert "**關鍵字:**" not in message
    assert (
        "**內容:** #售票文 售__**6/3**__內野__**118**__區25排15到18號有4張連號"
        in message
    )
    assert "__**6/3;118**__" not in message
    assert "**連結:** [開啟 Facebook 貼文](https://www.facebook.com/groups/1/posts/2)" in message


def test_discord_match_payload_escapes_untrusted_markdown() -> None:
    """Facebook 原文中的 Markdown 控制字元不可破壞 Discord 格式。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試_社團",
            item_kind="post",
            author="A*B",
            include_rule="6/3;118",
            text="售6/3_內野118*區 [測試](x)",
        )
    )

    assert "**社團:** 測試\\_社團" in message
    assert "**作者:** A\\*B" in message
    assert (
        "**內容:** 售__**6/3**__\\_內野__**118**__\\*區 \\[測試\\]\\(x\\)"
        in message
    )


def test_escape_discord_markdown_escapes_backslash_first() -> None:
    """反斜線本身也必須 escape，避免吞掉後續 Markdown escape。"""

    assert escape_discord_markdown(r"a\_b") == r"a\\\_b"
