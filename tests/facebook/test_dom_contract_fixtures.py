"""Frozen Facebook DOM fixture contract tests。

這裡不用真瀏覽器重跑 Facebook，而是用小型 HTML fixture 鎖住 posts/comments
extractor 依賴的基本 DOM 契約：feed target 找貼文 permalink，comments target
找留言 permalink 且保留 parent post id。
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

from facebook_monitor.facebook.comment_dom import COMMENTS_LIKE_ITEMS_SCRIPT
from facebook_monitor.facebook.feed_dom import POST_LIKE_ITEMS_SCRIPT
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
    actual = {
        "article_contracts": parser.article_contracts,
        "post_permalinks": [
            {
                "permalink": detail.permalink,
                "source": detail.source,
            }
            for detail in (
                extract_canonical_permalink_from_href(
                    href,
                    expected_group_id=GROUP_ID,
                )
                for href in parser.hrefs
            )
        ],
    }

    assert actual == _load_expected_snapshot("feed_dom/group_feed_minimal.expected.json")


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
    actual = {
        "article_contracts": parser.article_contracts,
        "comment_permalinks": [
            {
                "comment_id": detail.comment_id,
                "parent_post_id": PARENT_POST_ID,
                "permalink": detail.permalink,
            }
            for detail in comment_details
        ],
    }

    assert actual == _load_expected_snapshot(
        "comments_dom/post_comments_minimal.expected.json"
    )


def test_feed_dom_fixture_runs_actual_extractor_script() -> None:
    """posts fixture 應可被實際 feed DOM extractor 解析成 snapshot payload。"""

    html = _fixture_text("feed_dom/group_feed_minimal.html")
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}",
        html=html,
        script=POST_LIKE_ITEMS_SCRIPT,
        arg=5,
    )
    actual = {
        "items": [
            {
                "containerRole": item.get("containerRole"),
                "permalink": item.get("permalink"),
                "permalinkSource": item.get("permalinkSource"),
                "postId": item.get("postId"),
                "text": item.get("text"),
                "textSource": item.get("textSource"),
            }
            for item in payload["items"]
        ],
        "meta": {
            "articleElementCount": payload["meta"].get("articleElementCount"),
            "candidateCount": payload["meta"].get("candidateCount"),
            "filteredNonPostCount": payload["meta"].get("filteredNonPostCount"),
            "parsedCount": payload["meta"].get("parsedCount"),
            "postsWithPostIdCount": payload["meta"].get("postsWithPostIdCount"),
        },
    }

    assert actual == _load_expected_snapshot(
        "feed_dom/group_feed_minimal.extractor.expected.json"
    )


def test_feed_dom_extractor_preserves_story_message_newlines() -> None:
    """posts DOM extractor 應在 displayText 保留貼文內容換行。"""

    html = """
<!doctype html>
<html lang="zh-Hant">
<body>
  <main role="feed">
    <div role="article">
      <h2>賣家</h2>
      <div data-ad-preview="message">
        <div dir="auto">第一行票券<br>第二行座位</div>
      </div>
      <a href="https://www.facebook.com/groups/222518561920110/posts/1111111111111111/">時間</a>
    </div>
  </main>
</body>
</html>
"""
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}",
        html=html,
        script=POST_LIKE_ITEMS_SCRIPT,
        arg=5,
    )

    assert payload["items"][0]["text"] == "第一行票券 第二行座位"
    assert payload["items"][0]["displayText"] == "第一行票券\n第二行座位"


def test_comments_dom_fixture_runs_actual_extractor_script() -> None:
    """comments fixture 應可被實際 comments DOM extractor 解析成 snapshot payload。"""

    html = _fixture_text("comments_dom/post_comments_minimal.html")
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}",
        html=html,
        script=COMMENTS_LIKE_ITEMS_SCRIPT,
        arg={
            "groupId": GROUP_ID,
            "parentPostId": PARENT_POST_ID,
            "limit": 5,
        },
    )
    actual = {
        "items": [
            {
                "commentId": item.get("commentId"),
                "commentScopeReason": item.get("commentScopeReason"),
                "containerRole": item.get("containerRole"),
                "parentPostId": item.get("parentPostId"),
                "permalink": item.get("permalink"),
                "text": item.get("text"),
                "textSource": item.get("textSource"),
            }
            for item in payload["items"]
        ],
        "meta": {
            "articleElementCount": payload["meta"].get("articleElementCount"),
            "candidateCount": payload["meta"].get("candidateCount"),
            "commentsWithCommentIdCount": payload["meta"].get(
                "commentsWithCommentIdCount"
            ),
            "currentRouteMatchesTarget": payload["meta"].get(
                "currentRouteMatchesTarget"
            ),
            "filteredOutOfScopeCount": payload["meta"].get("filteredOutOfScopeCount"),
            "parsedCount": payload["meta"].get("parsedCount"),
        },
    }

    assert actual == _load_expected_snapshot(
        "comments_dom/post_comments_minimal.extractor.expected.json"
    )


def test_comments_dom_extractor_preserves_comment_newlines() -> None:
    """comments DOM extractor 應在 displayText 保留留言內容換行。"""

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="article">
      <span>留言者</span>
      <div dir="auto">第一行票券<br>第二行座位</div>
      <a href="https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=4444444444444444">回覆</a>
    </div>
  </main>
</body>
</html>
"""
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}",
        html=html,
        script=COMMENTS_LIKE_ITEMS_SCRIPT,
        arg={
            "groupId": GROUP_ID,
            "parentPostId": PARENT_POST_ID,
            "limit": 5,
        },
    )

    assert payload["items"][0]["text"] == "第一行票券 第二行座位"
    assert payload["items"][0]["displayText"] == "第一行票券\n第二行座位"


def test_comments_dom_extractor_excludes_author_link_span_from_text() -> None:
    """comments DOM extractor 不應把 author link span 併入留言正文。"""

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="article">
      <a role="link" href="https://www.facebook.com/profile.php?id=1">
        <span dir="auto">留言者</span>
      </a>
      <div dir="auto">第一行票券<br>第二行座位</div>
      <a href="https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=4444444444444444">回覆</a>
    </div>
  </main>
</body>
</html>
"""
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}",
        html=html,
        script=COMMENTS_LIKE_ITEMS_SCRIPT,
        arg={
            "groupId": GROUP_ID,
            "parentPostId": PARENT_POST_ID,
            "limit": 5,
        },
    )

    assert payload["items"][0]["text"] == "第一行票券 第二行座位"
    assert payload["items"][0]["displayText"] == "第一行票券\n第二行座位"
    assert "留言者" not in payload["items"][0]["displayText"]


def test_comments_dom_extractor_excludes_author_link_when_body_is_span() -> None:
    """留言正文是 span[dir=auto] 時，author link 仍不可被併入正文。"""

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="article">
      <a role="link" href="https://www.facebook.com/profile.php?id=1">
        <span dir="auto">留言者</span>
      </a>
      <span dir="auto">第一行票券</span>
      <span dir="auto">第二行座位</span>
      <a href="https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=4444444444444444">回覆</a>
    </div>
  </main>
</body>
</html>
"""
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}",
        html=html,
        script=COMMENTS_LIKE_ITEMS_SCRIPT,
        arg={
            "groupId": GROUP_ID,
            "parentPostId": PARENT_POST_ID,
            "limit": 5,
        },
    )

    assert payload["items"][0]["text"] == "第一行票券 第二行座位"
    assert payload["items"][0]["displayText"] == "第一行票券\n第二行座位"
    assert "留言者" not in payload["items"][0]["displayText"]


def test_comments_dom_extractor_keeps_first_body_span_without_author() -> None:
    """無明確作者節點時，不可把第一個正文 span 誤判成作者。"""

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="article">
      <span dir="auto">第一行票券</span>
      <span dir="auto">第二行座位</span>
      <a href="https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=4444444444444444">回覆</a>
    </div>
  </main>
</body>
</html>
"""
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}",
        html=html,
        script=COMMENTS_LIKE_ITEMS_SCRIPT,
        arg={
            "groupId": GROUP_ID,
            "parentPostId": PARENT_POST_ID,
            "limit": 5,
        },
    )

    assert payload["items"][0]["text"] == "第一行票券 第二行座位"
    assert payload["items"][0]["displayText"] == "第一行票券\n第二行座位"


def test_comments_dom_extractor_excludes_wrapped_author_link_from_text() -> None:
    """author link 與正文同包在外層 div[dir=auto] 時，外層不可污染正文。"""

    html = f"""
<!doctype html>
<html lang="zh-Hant">
<body>
  <main>
    <div role="article">
      <div dir="auto">
        <a role="link" href="https://www.facebook.com/profile.php?id=1">
          <span dir="auto">留言者</span>
        </a>
        <span dir="auto">第一行票券</span>
        <span dir="auto">第二行座位</span>
      </div>
      <a href="https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}/?comment_id=4444444444444444">回覆</a>
    </div>
  </main>
</body>
</html>
"""
    payload = _evaluate_fixture_page(
        url=f"https://www.facebook.com/groups/{GROUP_ID}/posts/{PARENT_POST_ID}",
        html=html,
        script=COMMENTS_LIKE_ITEMS_SCRIPT,
        arg={
            "groupId": GROUP_ID,
            "parentPostId": PARENT_POST_ID,
            "limit": 5,
        },
    )

    assert payload["items"][0]["text"] == "第一行票券 第二行座位"
    assert payload["items"][0]["displayText"] == "第一行票券\n第二行座位"
    assert "留言者" not in payload["items"][0]["displayText"]


def _parse_fixture(relative_path: str) -> FacebookContractParser:
    """讀取並解析單一 DOM fixture。"""

    parser = FacebookContractParser()
    parser.feed(_fixture_text(relative_path))
    return parser


def _load_expected_snapshot(relative_path: str) -> dict[str, Any]:
    """讀取 fixture 對應的 expected JSON snapshot。"""

    return json.loads((FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"))


def _fixture_text(relative_path: str) -> str:
    """讀取 DOM fixture HTML。"""

    return (FIXTURE_ROOT / relative_path).read_text(encoding="utf-8")


def _evaluate_fixture_page(
    *,
    url: str,
    html: str,
    script: str,
    arg: object,
) -> dict[str, Any]:
    """用 Playwright Chromium 執行實際 DOM extractor payload。"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=10_000)
        try:
            page = browser.new_page()
            _fulfill_fixture_route(page, url=url, html=html)
            page.goto(url)
            payload = page.evaluate(script, arg)
        finally:
            browser.close()
    if not isinstance(payload, dict):
        raise AssertionError("DOM extractor did not return an object payload")
    return payload


def _fulfill_fixture_route(page: Page, *, url: str, html: str) -> None:
    """用 fixture HTML 回應 Facebook URL，讓 extractor 可讀正確 location。"""

    page.route(
        f"{url.rstrip('/')}/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html,
        ),
    )
    page.route(
        url,
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html,
        ),
    )
