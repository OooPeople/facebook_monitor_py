"""Comments group-first navigation 純模型與 identity tests。"""

from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path

import pytest

from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationAnchorCandidate,
)
from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationAnchorSource,
)
from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationFreshnessEvidence,
)
from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationFreshnessOutcome,
)
from facebook_monitor.facebook.comments_navigation import CommentsNavigationOutcome
from facebook_monitor.facebook.comments_navigation import CommentsNavigationPhase
from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationPresentationEvidence,
)
from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationPresentationKind,
)
from facebook_monitor.facebook.comments_navigation import CommentsNavigationResult
from facebook_monitor.facebook.comments_navigation import (
    CommentsNavigationTargetIdentity,
)
from facebook_monitor.facebook.comments_navigation import (
    assess_comments_navigation_freshness,
)
from facebook_monitor.facebook.comments_navigation import (
    match_comments_navigation_anchor,
)
from facebook_monitor.facebook.comments_navigation import (
    select_comments_navigation_anchor,
)
from facebook_monitor.facebook.comments_navigation import (
    verify_comments_navigation_presentation,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "facebook" / "comments_navigation"
GROUP_ID = "111111111111111"
POST_ID = "222222222222222"
TARGET = CommentsNavigationTargetIdentity(GROUP_ID, POST_ID)


class _AnchorFixtureParser(HTMLParser):
    """把 sanitized HTML anchors 轉成 pure candidate facts。"""

    def __init__(self) -> None:
        super().__init__()
        self.candidates: list[CommentsNavigationAnchorCandidate] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        """收集 href、visibility 與 actionability，不建立 browser。"""

        if tag != "a":
            return
        values = dict(attrs)
        href = str(values.get("href") or "")
        self.candidates.append(
            CommentsNavigationAnchorCandidate(
                locator_index=len(self.candidates),
                href=href,
                visible="hidden" not in values and values.get("aria-hidden") != "true",
                actionable=("disabled" not in values and values.get("aria-disabled") != "true"),
            )
        )


def test_target_identity_normalizes_values_and_builds_identity_only_urls() -> None:
    """Target identity 提供 group cold-start URL 與只供比對的 post URL。"""

    target = CommentsNavigationTargetIdentity(f" {GROUP_ID} ", f" {POST_ID} ")

    assert target.is_valid
    assert target.group_url == f"https://www.facebook.com/groups/{GROUP_ID}"
    assert target.canonical_post_url == (
        f"https://www.facebook.com/groups/{GROUP_ID}/posts/{POST_ID}"
    )


@pytest.mark.parametrize(
    ("group_id", "post_id"),
    [
        ("", POST_ID),
        ("../groups/other", POST_ID),
        (GROUP_ID, "short"),
        (GROUP_ID, "1234567"),
    ],
)
def test_target_identity_rejects_values_that_are_not_safe_route_segments(
    group_id: str,
    post_id: str,
) -> None:
    """無效 target 不得產生可導航 URL。"""

    target = CommentsNavigationTargetIdentity(group_id, post_id)

    assert not target.is_valid
    assert target.group_url == ""
    assert target.canonical_post_url == ""


@pytest.mark.parametrize(
    ("href", "expected_source"),
    [
        (
            f"/groups/{GROUP_ID}/posts/{POST_ID}/",
            CommentsNavigationAnchorSource.GROUPS_POST,
        ),
        (
            f"https://facebook.com/groups/{GROUP_ID}/posts/{POST_ID}"
            "?__tn__=%2CO%2CP-R&mibextid=test",
            CommentsNavigationAnchorSource.GROUPS_POST,
        ),
        (
            f"https://m.facebook.com/groups/{GROUP_ID}/permalink/{POST_ID}/?ref=share",
            CommentsNavigationAnchorSource.GROUP_PERMALINK,
        ),
    ],
)
def test_anchor_match_accepts_only_safe_posts_and_permalink_variants(
    href: str,
    expected_source: CommentsNavigationAnchorSource,
) -> None:
    """允許的 route/query variants 仍需精確回到同一 group/post。"""

    match = match_comments_navigation_anchor(
        _candidate(href),
        target=TARGET,
    )

    assert match is not None
    assert match.source == expected_source
    assert match.canonical_url == TARGET.canonical_post_url


@pytest.mark.parametrize(
    "href",
    [
        f"/groups/999999999999999/posts/{POST_ID}",
        f"/groups/{GROUP_ID}/posts/999999999999999",
        f"/groups/{GROUP_ID}/posts/{POST_ID}?comment_id=333333333333333",
        f"/groups/{GROUP_ID}/posts/{POST_ID}?reply_comment_id=333333333333333",
        f"/groups/{GROUP_ID}/posts/{POST_ID}?unknown_tracking=value",
        f"/groups/{GROUP_ID}/posts/{POST_ID}#fragment",
        f"https://www.facebook.com/photo.php?fbid=1&set=gm.{POST_ID}",
        "https://www.facebook.com/share/p/example-token/",
        f"https://example.invalid/groups/{GROUP_ID}/posts/{POST_ID}",
        f"https://evilfacebook.com/groups/{GROUP_ID}/posts/{POST_ID}",
        f"http://www.facebook.com/groups/{GROUP_ID}/posts/{POST_ID}",
        f"https://user@example.com/groups/{GROUP_ID}/posts/{POST_ID}",
        f"https://www.facebook.com:444/groups/{GROUP_ID}/posts/{POST_ID}",
        f"https://www.facebook.com:not-a-port/groups/{GROUP_ID}/posts/{POST_ID}",
        f"/groups/{GROUP_ID}/posts/{POST_ID}/extra",
    ],
)
def test_anchor_match_rejects_cross_scope_comment_photo_share_and_unsafe_urls(
    href: str,
) -> None:
    """Navigation identity 不沿用 extractor 對 photo/comment permalink 的寬鬆支援。"""

    assert match_comments_navigation_anchor(_candidate(href), target=TARGET) is None


def test_fixture_selection_prefers_visible_actionable_posts_anchor() -> None:
    """Hidden/disabled exact anchors 不可蓋過真正可操作的 posts anchor。"""

    candidates = _load_anchor_fixture("group_anchor_candidates.html")
    expected = json.loads(
        (FIXTURE_ROOT / "group_anchor_candidates.expected.json").read_text(encoding="utf-8")
    )

    selection = select_comments_navigation_anchor(candidates, target=TARGET)

    assert selection.candidate_count == expected["candidate_count"]
    assert selection.exact_match_count == expected["exact_match_count"]
    assert selection.actionable_match_count == expected["actionable_match_count"]
    assert selection.selected is not None
    assert selection.selected.candidate.locator_index == expected["selected_locator_index"]
    assert selection.selected.source.value == expected["selected_source"]
    assert "href" not in selection.to_diagnostics()


def test_fixture_selection_returns_no_match_for_old_post_missing_from_dom() -> None:
    """舊貼文不在目前 group DOM 時只回無選擇，不猜測或 fallback。"""

    selection = select_comments_navigation_anchor(
        _load_anchor_fixture("group_anchor_missing.html"),
        target=TARGET,
    )

    assert selection.selected is None
    assert selection.exact_match_count == 0
    assert selection.actionable_match_count == 0


def test_hidden_and_non_actionable_exact_anchors_are_not_selected() -> None:
    """即使 href identity 精確，hidden/disabled anchors 仍不可 trusted click。"""

    selection = select_comments_navigation_anchor(
        [
            _candidate(TARGET.canonical_post_url, visible=False),
            _candidate(TARGET.canonical_post_url, actionable=False, locator_index=1),
        ],
        target=TARGET,
    )

    assert selection.exact_match_count == 2
    assert selection.actionable_match_count == 0
    assert selection.selected is None


def test_routed_post_presentation_requires_route_dom_guard_and_generation() -> None:
    """Routed post 只有 URL、DOM、guard 與 trusted generation 全部吻合才通過。"""

    proof = verify_comments_navigation_presentation(
        target=TARGET,
        evidence=CommentsNavigationPresentationEvidence(
            kind=CommentsNavigationPresentationKind.ROUTED_POST,
            current_url=f"{TARGET.canonical_post_url}?ref=share",
            dom_group_id=GROUP_ID,
            dom_post_id=POST_ID,
            post_container_visible=True,
            page_guard_passed=True,
            trusted_click_generation="navigation-1",
            current_navigation_generation="navigation-1",
        ),
    )

    assert proof.verified
    assert proof.route_identity_verified
    assert proof.dom_identity_verified
    assert proof.trusted_generation_verified


def test_group_modal_presentation_uses_origin_group_and_visible_dialog_identity() -> None:
    """Modal 不硬要求 post URL，但必須保留 origin group 與 exact dialog identity。"""

    proof = verify_comments_navigation_presentation(
        target=TARGET,
        evidence=CommentsNavigationPresentationEvidence(
            kind=CommentsNavigationPresentationKind.GROUP_MODAL,
            current_url=f"{TARGET.group_url}?sorting_setting=CHRONOLOGICAL",
            dom_group_id=GROUP_ID,
            dom_post_id=POST_ID,
            post_container_visible=True,
            dialog_visible=True,
            page_guard_passed=True,
            trusted_click_generation="navigation-2",
            current_navigation_generation="navigation-2",
        ),
    )

    assert proof.verified
    assert proof.kind == CommentsNavigationPresentationKind.GROUP_MODAL


@pytest.mark.parametrize(
    "evidence",
    [
        CommentsNavigationPresentationEvidence(
            kind=CommentsNavigationPresentationKind.ROUTED_POST,
            current_url=f"{TARGET.canonical_post_url}?comment_id=333333333333333",
            dom_group_id=GROUP_ID,
            dom_post_id=POST_ID,
            post_container_visible=True,
            page_guard_passed=True,
            trusted_click_generation="navigation-1",
            current_navigation_generation="navigation-1",
        ),
        CommentsNavigationPresentationEvidence(
            kind=CommentsNavigationPresentationKind.GROUP_MODAL,
            current_url=TARGET.group_url,
            dom_group_id=GROUP_ID,
            dom_post_id=POST_ID,
            post_container_visible=True,
            dialog_visible=False,
            page_guard_passed=True,
            trusted_click_generation="navigation-1",
            current_navigation_generation="navigation-1",
        ),
        CommentsNavigationPresentationEvidence(
            kind=CommentsNavigationPresentationKind.GROUP_MODAL,
            current_url=TARGET.group_url,
            dom_group_id=GROUP_ID,
            dom_post_id=POST_ID,
            post_container_visible=True,
            dialog_visible=True,
            page_guard_passed=True,
            trusted_click_generation="navigation-1",
            current_navigation_generation="navigation-stale",
        ),
    ],
)
def test_presentation_rejects_comment_route_hidden_dialog_and_stale_generation(
    evidence: CommentsNavigationPresentationEvidence,
) -> None:
    """可疑 route、不可見 modal 或 stale generation 都不能建立 trusted proof。"""

    proof = verify_comments_navigation_presentation(target=TARGET, evidence=evidence)

    assert not proof.verified


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            CommentsNavigationFreshnessEvidence(
                navigation_generation="navigation-1",
                new_comment_identity_seen=True,
            ),
            CommentsNavigationFreshnessOutcome.CONFIRMED_NEW_COMMENT,
        ),
        (
            CommentsNavigationFreshnessEvidence(
                navigation_generation="navigation-2",
                trusted_click_completed=True,
                post_root_replaced=True,
                facebook_fetch_xhr_completed_count=1,
            ),
            CommentsNavigationFreshnessOutcome.CONFIRMED_REPLACED_ROOT,
        ),
        (
            CommentsNavigationFreshnessEvidence(
                navigation_generation="navigation-3",
                explicit_refresh_signal_seen=True,
            ),
            CommentsNavigationFreshnessOutcome.CONFIRMED_UI_SIGNAL,
        ),
        (
            CommentsNavigationFreshnessEvidence(
                navigation_generation="navigation-4",
                trusted_click_completed=True,
                post_root_replaced=True,
                facebook_fetch_xhr_completed_count=0,
            ),
            CommentsNavigationFreshnessOutcome.UNCONFIRMED,
        ),
        (
            CommentsNavigationFreshnessEvidence(
                navigation_generation="",
                new_comment_identity_seen=True,
                facebook_fetch_xhr_completed_count=-3,
            ),
            CommentsNavigationFreshnessOutcome.UNCONFIRMED,
        ),
    ],
)
def test_freshness_assessment_requires_bounded_runtime_evidence(
    evidence: CommentsNavigationFreshnessEvidence,
    expected: CommentsNavigationFreshnessOutcome,
) -> None:
    """Route open或同一DOM重讀不構成 freshness confirmed。"""

    assessment = assess_comments_navigation_freshness(evidence)

    assert assessment.outcome == expected
    assert assessment.confirmed is (expected != CommentsNavigationFreshnessOutcome.UNCONFIRMED)
    assert assessment.fetch_xhr_completed_count >= 0


def test_navigation_result_requires_verified_presentation_and_freshness() -> None:
    """Typed result 不讓 READY label 單獨繞過 proof/freshness gate。"""

    presentation = verify_comments_navigation_presentation(
        target=TARGET,
        evidence=CommentsNavigationPresentationEvidence(
            kind=CommentsNavigationPresentationKind.ROUTED_POST,
            current_url=TARGET.canonical_post_url,
            dom_group_id=GROUP_ID,
            dom_post_id=POST_ID,
            post_container_visible=True,
            page_guard_passed=True,
            trusted_click_generation="navigation-ready",
            current_navigation_generation="navigation-ready",
        ),
    )
    freshness = assess_comments_navigation_freshness(
        CommentsNavigationFreshnessEvidence(
            navigation_generation="navigation-ready",
            new_comment_identity_seen=True,
        )
    )
    result = CommentsNavigationResult(
        target=TARGET,
        phase=CommentsNavigationPhase.READY_FOR_SCAN,
        outcome=CommentsNavigationOutcome.READY,
        presentation=presentation,
        freshness=freshness,
    )

    assert result.ready_for_scan
    diagnostics = result.to_diagnostics()
    assert diagnostics["ready_for_scan"] is True
    assert GROUP_ID not in json.dumps(diagnostics)
    assert POST_ID not in json.dumps(diagnostics)


def _candidate(
    href: str,
    *,
    visible: bool = True,
    actionable: bool = True,
    locator_index: int = 0,
) -> CommentsNavigationAnchorCandidate:
    """建立單元測試使用的最小 candidate。"""

    return CommentsNavigationAnchorCandidate(
        locator_index=locator_index,
        href=href,
        visible=visible,
        actionable=actionable,
    )


def _load_anchor_fixture(file_name: str) -> list[CommentsNavigationAnchorCandidate]:
    """載入去識別 HTML fixture 並轉成候選 facts。"""

    parser = _AnchorFixtureParser()
    parser.feed((FIXTURE_ROOT / file_name).read_text(encoding="utf-8"))
    return parser.candidates
