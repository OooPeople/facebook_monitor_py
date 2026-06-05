"""Discord notification formatter tests。"""

from __future__ import annotations

from facebook_monitor.notifications.discord_format import build_discord_match_notification_payload
from facebook_monitor.notifications.discord_format import escape_discord_markdown
from facebook_monitor.notifications.discord_format import format_discord_link_url
from facebook_monitor.notifications.discord_format import strip_ansi_escape_sequences
from facebook_monitor.notifications.payload import MatchNotificationFields


def test_discord_match_payload_uses_text_layout_with_keyword_highlight() -> None:
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
        "# * Facebook keyword match",
        "社團：中信兄弟商品及門票 代購轉售",
        "類型：貼文",
        "作者：陳建宇",
        "命中：6/3  ,  118",
        "",
        "#售票文 售**6/3**內野**118**區25排15到18號有4張連號",
        "",
        "<https://www.facebook.com/groups/1/posts/2>",
    ]
    assert "社團:" not in message
    assert "類型:" not in message
    assert "作者:" not in message
    assert "命中:" not in message
    assert "內容:" not in message
    assert "關鍵字:" not in message
    assert "連結:" not in message
    assert "開啟連結：" not in message
    assert "[開啟連結]" not in message
    assert "```" not in message
    assert "\x1b" not in message
    assert message.startswith("# * Facebook keyword match\n社團：")
    assert "命中：6/3  ,  118\n\n#售票文" in message
    assert (
        "#售票文 售**6/3**內野**118**區25排15到18號有4張連號"
        "\n\n<https://www.facebook.com/groups/1/posts/2>"
    ) in message
    assert "━" not in message


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

    assert "命中：6/5  ,  110  ,  114" in message
    assert "命中：6/5  ,  110  ,  114\n\n**6/5**" in message
    assert (
        "**6/5** **110** 12排小號 電子票 1080\n"
        "以下位置不含開場舞時間 需自行補票\n"
        "6/6 **114** 7排890\n"
        "6/7 **114** 9排790"
    ) in message
    assert "內容:" not in message
    assert "\x1b" not in message


def test_discord_match_payload_escapes_markdown_around_content_highlight() -> None:
    """未命中內容仍要 escape Markdown，避免原文誤觸格式。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試_社團",
            item_kind="post",
            author="A*B",
            include_rule="6/3;118;[票券](evil)",
            text="售6/3_內野118*區 [測試](x)",
        )
    )

    assert "社團：測試\\_社團" in message
    assert "作者：A\\*B" in message
    assert "命中：6/3  ,  118  ,  \\[票券\\]\\(evil\\)" in message
    assert "售**6/3**\\_內野**118**\\*區 \\[測試\\]\\(x\\)" in message
    assert "內容:" not in message


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

    assert message.count("第一行") == 1
    assert "**票券**第一行\n第二行座位" in message


def test_discord_match_payload_escapes_body_backticks() -> None:
    """Facebook 原文 backticks 會以 Markdown escape 維持純文字。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="測試使用者",
            include_rule="票券",
            text="第一行```票券\n第二行````座位",
        )
    )

    assert "```" not in message
    assert "第一行\\`\\`\\`**票券**" in message
    assert "第二行\\`\\`\\`\\`座位" in message


def test_discord_match_payload_strips_source_ansi_escape_codes() -> None:
    """Facebook 原文 ESC 控制字元不可注入 Discord content。"""

    _title, message = build_discord_match_notification_payload(
        MatchNotificationFields(
            group_name="測試社團",
            item_kind="post",
            author="測試使用者",
            include_rule="票券",
            text="售\x1b[31m票券\x1b[0m $700元/張",
        )
    )

    assert "\x1b" not in message
    assert "[31m" not in message
    assert "售**票券** $700元/張" in message
    assert "$700元/張" in message


def test_escape_discord_markdown_escapes_backslash_first() -> None:
    """反斜線本身也必須 escape，避免吞掉後續 Markdown escape。"""

    assert escape_discord_markdown(r"a\_b") == r"a\\\_b"


def test_strip_ansi_escape_sequences_removes_source_esc() -> None:
    """原文中的 ANSI sequence 和裸 ESC 會被移除。"""

    assert strip_ansi_escape_sequences("a\x1b[31mb\x1bc") == "abc"


def test_format_discord_link_url_suppresses_embed_preview() -> None:
    """Discord link 使用 angle wrapper 取消預覽，並整理會破壞語法的字元。"""

    assert (
        format_discord_link_url("https://example.com/a b)c<d>")
        == "<https://example.com/a%20b%29c%3Cd%3E>"
    )
