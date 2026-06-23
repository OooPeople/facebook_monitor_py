"""Facebook permalink helper boundary tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.facebook.permalink import extract_canonical_permalink_from_href
from facebook_monitor.facebook.permalink import extract_comment_permalink_details


@pytest.mark.parametrize(
    "href",
    [
        "https://www.facebook.com/permalink.php?"
        "story_fbid=1234567890123456&id=222518561920110",
        "https://www.facebook.com/photo.php?"
        "fbid=999&set=gm.1234567890123456&idorvanity=222518561920110",
        "https://www.facebook.com/groups/222518561920110?"
        "multi_permalinks=1234567890123456",
        "https://www.facebook.com/groups/222518561920110/posts/pcb.1234567890123456",
    ],
)
def test_permalink_variants_reject_other_group_when_expected_group_is_set(
    href: str,
) -> None:
    """所有 post permalink 變體都不可跨 expected group 污染 target。"""

    details = extract_canonical_permalink_from_href(
        href,
        expected_group_id="999999999999999",
    )

    assert details.permalink == ""
    assert details.source == ""


def test_comment_permalink_uses_same_group_route_post_id_before_parent_fallback() -> None:
    """comment permalink route 內的同 group post id 比 caller fallback parent id 更可信。"""

    details = extract_comment_permalink_details(
        "https://www.facebook.com/groups/222518561920110/posts/3333333333333333"
        "/?comment_id=5555555555555555",
        group_id="222518561920110",
        parent_post_id="4444444444444444",
    )

    assert details.permalink == (
        "https://www.facebook.com/groups/222518561920110/posts/"
        "3333333333333333/?comment_id=5555555555555555"
    )
    assert details.comment_id == "5555555555555555"
    assert details.source == "comment_anchor"
