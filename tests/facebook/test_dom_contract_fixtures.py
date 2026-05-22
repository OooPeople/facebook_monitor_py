"""Frozen Facebook DOM fixture contract tests。

這裡不用真瀏覽器重跑 Facebook，而是用小型 HTML fixture 鎖住 posts/comments
extractor 依賴的基本 DOM 契約：feed target 找貼文 permalink，comments target
找留言 permalink 且保留 parent post id。
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from facebook_monitor.facebook.permalink import extract_canonical_permalink_from_href
from facebook_monitor.facebook.permalink import extract_comment_permalink_details


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "facebook"
GROUP_ID = "222518561920110"
PARENT_POST_ID = "9999999999999999"


class FacebookContractParser(HTMLParser):
    """擷取 fixture 中 contract 標記、role 與 anchor href。"""

    def __init__(self) -> None:
        super().__init__()
        self.contracts: list[str] = []
        self.article_contracts: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """記錄 extractor contract tests 需要的少量 DOM 屬性。"""

        values = dict(attrs)
        contract = str(values.get("data-contract") or "")
        if contract:
            self.contracts.append(contract)
        if values.get("role") == "article" and contract:
            self.article_contracts.append(contract)
        href = values.get("href")
        if tag == "a" and href:
            self.hrefs.append(href)


def test_feed_dom_fixture_preserves_post_permalink_contract() -> None:
    """posts feed fixture 應解析出 group-scoped post permalink，不吃成留言 target。"""

    parser = _parse_fixture("feed_dom/group_feed_minimal.html")
    details = [
        extract_canonical_permalink_from_href(href, expected_group_id=GROUP_ID)
        for href in parser.hrefs
    ]

    assert parser.article_contracts == ["feed-post", "feed-post"]
    assert [detail.source for detail in details] == [
        "groups_post_anchor",
        "groups_post_anchor",
    ]
    assert [detail.permalink for detail in details] == [
        f"https://www.facebook.com/groups/{GROUP_ID}/posts/1111111111111111",
        f"https://www.facebook.com/groups/{GROUP_ID}/posts/2222222222222222",
    ]


def test_comments_dom_fixture_preserves_comment_permalink_contract() -> None:
    """comments fixture 應保留 parent post id，並只把 comment anchors 當留言。"""

    parser = _parse_fixture("comments_dom/post_comments_minimal.html")
    comment_details = [
        extract_comment_permalink_details(
            href,
            group_id=GROUP_ID,
            parent_post_id=PARENT_POST_ID,
        )
        for href in parser.hrefs
    ]
    comment_details = [detail for detail in comment_details if detail.comment_id]

    assert parser.article_contracts == ["parent-post", "comment", "comment"]
    assert [detail.comment_id for detail in comment_details] == [
        "4444444444444444",
        "5555555555555555",
    ]
    assert [detail.permalink for detail in comment_details] == [
        f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=4444444444444444",
        f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=5555555555555555",
    ]


def _parse_fixture(relative_path: str) -> FacebookContractParser:
    """讀取並解析單一 DOM fixture。"""

    parser = FacebookContractParser()
    parser.feed((FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"))
    return parser
