"""Dedupe key pure logic tests。"""

from __future__ import annotations

from facebook_monitor.core.dedupe import ScanItemIdentity
from facebook_monitor.core.dedupe import aliases_overlap
from facebook_monitor.core.dedupe import build_legacy_item_key
from facebook_monitor.core.dedupe import get_item_key_aliases
from facebook_monitor.core.dedupe import get_primary_item_key
from facebook_monitor.core.dedupe import get_raw_item_key_aliases


def test_post_key_aliases_include_id_url_composite_and_legacy_key() -> None:
    """有 permalink 的貼文會同時產生 id/url/composite 與舊版 key。"""

    item = ScanItemIdentity(
        text="這是一篇有票券關鍵字的貼文",
        permalink="https://www.facebook.com/groups/222518561920110/posts/1234567890",
        author="王小明",
    )

    raw_aliases = get_raw_item_key_aliases(item)
    aliases = get_item_key_aliases(item)

    assert raw_aliases[0] == "id:1234567890"
    assert "url:https://www.facebook.com/groups/222518561920110/posts/1234567890" in raw_aliases
    assert any(alias.startswith("author:王小明||text:") for alias in raw_aliases)
    assert build_legacy_item_key(item) in aliases


def test_aliases_overlap_when_permalink_disappears_but_author_text_remain() -> None:
    """下一輪若 permalink 消失，仍可用作者與文字 alias 判斷同一篇。"""

    with_permalink = get_item_key_aliases(
        ScanItemIdentity(
            text="這是一篇有票券關鍵字的貼文",
            permalink="https://www.facebook.com/groups/222518561920110/posts/1234567890",
            author="王小明",
        )
    )
    without_permalink = get_item_key_aliases(
        ScanItemIdentity(
            text="這是一篇有票券關鍵字的貼文",
            permalink="",
            author="王小明",
        )
    )

    assert aliases_overlap(with_permalink, without_permalink)


def test_comment_key_uses_comment_id_before_permalink_and_fallback() -> None:
    """comments item key 對齊 JS：commentId 優先，再退回 permalink / parent composite。"""

    identity = ScanItemIdentity(
        item_kind="comment",
        parent_post_id="2187454285426518",
        comment_id="9876543210987654",
        permalink=(
            "https://www.facebook.com/groups/222518561920110/posts/"
            "2187454285426518/?comment_id=9876543210987654"
        ),
        author="留言作者",
        text="這是一則留言",
    )

    raw_aliases = get_raw_item_key_aliases(identity)

    assert raw_aliases[0] == "comment:9876543210987654"
    assert raw_aliases[1].startswith("comment-url:")
    assert any(alias.startswith("post:2187454285426518||") for alias in raw_aliases)
    assert get_primary_item_key(identity).startswith("v2:")


def test_comment_aliases_do_not_overlap_by_parent_post_legacy_permalink() -> None:
    """同一篇貼文下不同留言不能因 post legacy permalink 被視為同一項。"""

    first = get_item_key_aliases(
        ScanItemIdentity(
            item_kind="comment",
            parent_post_id="2187454285426518",
            comment_id="9876543210987654",
            permalink=(
                "https://www.facebook.com/groups/222518561920110/posts/"
                "2187454285426518/?comment_id=9876543210987654"
            ),
            author="留言作者 A",
            text="第一則留言",
        )
    )
    second = get_item_key_aliases(
        ScanItemIdentity(
            item_kind="comment",
            parent_post_id="2187454285426518",
            comment_id="9876543210987655",
            permalink=(
                "https://www.facebook.com/groups/222518561920110/posts/"
                "2187454285426518/?comment_id=9876543210987655"
            ),
            author="留言作者 B",
            text="第二則留言",
        )
    )

    assert not aliases_overlap(first, second)
