"""Facebook comments 站內導覽的純資料模型與 identity helpers。

職責：定義 comments target、真實 anchor 候選、presentation proof、freshness
evidence 與 navigation result。此模組不依賴 Playwright、worker、DB 或 runtime，
讓後續 resident state machine 只負責收集 evidence 與執行 trusted click。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any
from urllib.parse import parse_qsl
from urllib.parse import urljoin
from urllib.parse import urlparse

from facebook_monitor.core.permalink_identity import build_canonical_group_post_url
from facebook_monitor.facebook.route_detection import FACEBOOK_HOSTS


_FACEBOOK_BASE_URL = "https://www.facebook.com"
_SAFE_GROUP_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+")
_SAFE_POST_ID_PATTERN = re.compile(r"\d{8,}")
_GROUP_POST_PATH_PATTERN = re.compile(
    r"^/groups/([^/?#]+)/(posts|permalink)/(\d{8,})/?$",
    re.IGNORECASE,
)
_SAFE_POST_QUERY_KEYS = frozenset(
    {
        "__cft__[0]",
        "__tn__",
        "eav",
        "mibextid",
        "paipv",
        "ref",
    }
)
_SAFE_GROUP_QUERY_KEYS = frozenset({"sorting_setting"})


class CommentsNavigationPhase(StrEnum):
    """Comments navigation state machine 的穩定階段。"""

    COLD_START = "cold_start"
    GROUP_LOADING = "group_loading"
    LINK_DISCOVERY = "link_discovery"
    TRUSTED_CLICK = "trusted_click"
    POST_VERIFY = "post_verify"
    READY_FOR_SCAN = "ready_for_scan"
    RETURN_TO_GROUP = "return_to_group"
    BACKING_OFF = "backing_off"


class CommentsNavigationOutcome(StrEnum):
    """Comments navigation 的穩定結果原因。"""

    READY = "ready"
    GROUP_UNAVAILABLE = "group_unavailable"
    LINK_NOT_FOUND = "link_not_found"
    LINK_NOT_ACTIONABLE = "link_not_actionable"
    CLICK_FAILED = "click_failed"
    ROUTE_MISMATCH = "route_mismatch"
    POST_NOT_SCANNABLE = "post_not_scannable"
    REFRESH_PATH_UNAVAILABLE = "refresh_path_unavailable"
    TRUSTED_STATE_LOST = "trusted_state_lost"
    FRESHNESS_UNCONFIRMED = "freshness_unconfirmed"
    TEMPORARY_BLOCK = "temporary_block"
    STOPPED = "stopped"


class CommentsNavigationAnchorSource(StrEnum):
    """允許用於 group-first trusted click 的 anchor route 類型。"""

    GROUPS_POST = "groups_post_anchor"
    GROUP_PERMALINK = "group_permalink_anchor"


class CommentsNavigationPresentationKind(StrEnum):
    """Trusted click 後可接受的 Facebook 貼文呈現型態。"""

    ROUTED_POST = "routed_post"
    GROUP_MODAL = "group_modal"


class CommentsNavigationFreshnessOutcome(StrEnum):
    """本輪 comments snapshot 是否具有可接受的 freshness evidence。"""

    CONFIRMED_NEW_COMMENT = "confirmed_new_comment"
    CONFIRMED_REPLACED_ROOT = "confirmed_replaced_root"
    CONFIRMED_UI_SIGNAL = "confirmed_ui_signal"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class CommentsNavigationTargetIdentity:
    """保存 group-first comments navigation 的預期 target identity。"""

    group_id: str
    parent_post_id: str

    def __post_init__(self) -> None:
        """正規化 caller 傳入的字串，避免空白造成 identity 漂移。"""

        object.__setattr__(self, "group_id", str(self.group_id or "").strip())
        object.__setattr__(
            self,
            "parent_post_id",
            str(self.parent_post_id or "").strip(),
        )

    @property
    def is_valid(self) -> bool:
        """回傳 identity 是否能安全放入 Facebook group route。"""

        return bool(
            _SAFE_GROUP_ID_PATTERN.fullmatch(self.group_id)
            and _SAFE_POST_ID_PATTERN.fullmatch(self.parent_post_id)
        )

    @property
    def group_url(self) -> str:
        """回傳唯一允許 cold-start document navigation 的 group URL。"""

        if not self.is_valid:
            return ""
        return f"{_FACEBOOK_BASE_URL}/groups/{self.group_id}"

    @property
    def canonical_post_url(self) -> str:
        """回傳只供 identity 比對的 canonical post URL，不代表允許 goto。"""

        if not self.is_valid:
            return ""
        return build_canonical_group_post_url(self.group_id, self.parent_post_id)


@dataclass(frozen=True)
class CommentsNavigationAnchorCandidate:
    """保存從 group DOM 收集的最小 anchor facts。"""

    locator_index: int
    href: str
    visible: bool
    actionable: bool


@dataclass(frozen=True)
class CommentsNavigationAnchorMatch:
    """保存一個與 target identity 完全吻合的 anchor。"""

    candidate: CommentsNavigationAnchorCandidate
    source: CommentsNavigationAnchorSource
    canonical_url: str


@dataclass(frozen=True)
class CommentsNavigationAnchorSelection:
    """保存 bounded anchor discovery 的純選擇結果。"""

    candidate_count: int
    exact_match_count: int
    actionable_match_count: int
    selected: CommentsNavigationAnchorMatch | None = None

    def to_diagnostics(self) -> dict[str, object]:
        """輸出不含 raw href 或 target identity 的安全 diagnostics。"""

        return {
            "candidate_count": self.candidate_count,
            "exact_match_count": self.exact_match_count,
            "actionable_match_count": self.actionable_match_count,
            "selected": self.selected is not None,
            "anchor_source": self.selected.source.value if self.selected else "",
        }


@dataclass(frozen=True)
class CommentsNavigationPresentationEvidence:
    """保存 trusted click 後由 runtime 收集的 presentation facts。"""

    kind: CommentsNavigationPresentationKind
    current_url: str
    dom_group_id: str
    dom_post_id: str
    post_container_visible: bool
    page_guard_passed: bool
    trusted_click_generation: str
    current_navigation_generation: str
    dialog_visible: bool = False


@dataclass(frozen=True)
class CommentsNavigationPresentationProof:
    """保存 routed post 或 group modal 的 identity 驗證結果。"""

    kind: CommentsNavigationPresentationKind
    route_identity_verified: bool
    dom_identity_verified: bool
    trusted_generation_verified: bool
    page_guard_passed: bool
    verified: bool

    def to_diagnostics(self) -> dict[str, object]:
        """輸出 presentation 的 privacy-safe boolean diagnostics。"""

        return {
            "presentation_kind": self.kind.value,
            "route_identity_verified": self.route_identity_verified,
            "dom_identity_verified": self.dom_identity_verified,
            "trusted_generation_verified": self.trusted_generation_verified,
            "page_guard_passed": self.page_guard_passed,
            "presentation_verified": self.verified,
        }


@dataclass(frozen=True)
class CommentsNavigationFreshnessEvidence:
    """保存本輪重新開啟貼文後可觀測的 freshness facts。"""

    navigation_generation: str
    trusted_click_completed: bool = False
    post_root_replaced: bool = False
    facebook_fetch_xhr_completed_count: int = 0
    new_comment_identity_seen: bool = False
    explicit_refresh_signal_seen: bool = False


@dataclass(frozen=True)
class CommentsNavigationFreshnessAssessment:
    """保存 freshness evidence 的純判斷結果。"""

    outcome: CommentsNavigationFreshnessOutcome
    confirmed: bool
    post_root_replaced: bool
    fetch_xhr_completed_count: int
    new_comment_identity_seen: bool
    explicit_refresh_signal_seen: bool

    def to_diagnostics(self) -> dict[str, object]:
        """輸出 bounded count 與 boolean，不輸出 comment identity。"""

        return {
            "freshness_outcome": self.outcome.value,
            "freshness_confirmed": self.confirmed,
            "post_root_replaced": self.post_root_replaced,
            "fetch_xhr_completed_count": self.fetch_xhr_completed_count,
            "new_comment_identity_seen": self.new_comment_identity_seen,
            "explicit_refresh_signal_seen": self.explicit_refresh_signal_seen,
        }


@dataclass(frozen=True)
class CommentsNavigationResult:
    """保存純 navigation state machine 可交給 worker 的 typed result。"""

    target: CommentsNavigationTargetIdentity
    phase: CommentsNavigationPhase
    outcome: CommentsNavigationOutcome
    anchor_selection: CommentsNavigationAnchorSelection | None = None
    presentation: CommentsNavigationPresentationProof | None = None
    freshness: CommentsNavigationFreshnessAssessment | None = None

    @property
    def ready_for_scan(self) -> bool:
        """只有 presentation 與 freshness 都確認時才允許進 scanner。"""

        return bool(
            self.phase == CommentsNavigationPhase.READY_FOR_SCAN
            and self.outcome == CommentsNavigationOutcome.READY
            and self.presentation is not None
            and self.presentation.verified
            and self.freshness is not None
            and self.freshness.confirmed
        )

    def to_diagnostics(self) -> dict[str, Any]:
        """合併不含 raw href、URL 或 identity 的安全 diagnostics。"""

        diagnostics: dict[str, Any] = {
            "phase": self.phase.value,
            "outcome": self.outcome.value,
            "ready_for_scan": self.ready_for_scan,
        }
        if self.anchor_selection is not None:
            diagnostics.update(self.anchor_selection.to_diagnostics())
        if self.presentation is not None:
            diagnostics.update(self.presentation.to_diagnostics())
        if self.freshness is not None:
            diagnostics.update(self.freshness.to_diagnostics())
        return diagnostics


def match_comments_navigation_anchor(
    candidate: CommentsNavigationAnchorCandidate,
    *,
    target: CommentsNavigationTargetIdentity,
) -> CommentsNavigationAnchorMatch | None:
    """解析允許的 posts/permalink href，並嚴格比對 target identity。"""

    parsed_match = _parse_safe_group_post_href(candidate.href)
    if parsed_match is None or not target.is_valid:
        return None
    group_id, post_id, source = parsed_match
    if group_id != target.group_id or post_id != target.parent_post_id:
        return None
    return CommentsNavigationAnchorMatch(
        candidate=candidate,
        source=source,
        canonical_url=target.canonical_post_url,
    )


def select_comments_navigation_anchor(
    candidates: tuple[CommentsNavigationAnchorCandidate, ...]
    | list[CommentsNavigationAnchorCandidate],
    *,
    target: CommentsNavigationTargetIdentity,
) -> CommentsNavigationAnchorSelection:
    """從 bounded DOM candidates 選出可信度最高的可操作 exact anchor。"""

    exact_matches = tuple(
        match
        for candidate in candidates
        if (match := match_comments_navigation_anchor(candidate, target=target)) is not None
    )
    actionable_matches = tuple(
        match for match in exact_matches if match.candidate.visible and match.candidate.actionable
    )
    selected = min(
        actionable_matches,
        key=lambda match: (
            _anchor_source_priority(match.source),
            max(match.candidate.locator_index, 0),
        ),
        default=None,
    )
    return CommentsNavigationAnchorSelection(
        candidate_count=len(candidates),
        exact_match_count=len(exact_matches),
        actionable_match_count=len(actionable_matches),
        selected=selected,
    )


def verify_comments_navigation_presentation(
    *,
    target: CommentsNavigationTargetIdentity,
    evidence: CommentsNavigationPresentationEvidence,
) -> CommentsNavigationPresentationProof:
    """依 routed post / group modal 的獨立契約驗證 trusted presentation。"""

    trusted_generation_verified = bool(
        evidence.trusted_click_generation
        and evidence.trusted_click_generation == evidence.current_navigation_generation
    )
    dom_identity_verified = bool(
        evidence.post_container_visible
        and evidence.dom_group_id == target.group_id
        and evidence.dom_post_id == target.parent_post_id
    )
    if evidence.kind == CommentsNavigationPresentationKind.ROUTED_POST:
        parsed_match = _parse_safe_group_post_href(evidence.current_url)
        route_identity_verified = bool(
            parsed_match is not None
            and parsed_match[0] == target.group_id
            and parsed_match[1] == target.parent_post_id
        )
        presentation_visible = evidence.post_container_visible
    else:
        route_identity_verified = _matches_safe_group_route(
            evidence.current_url,
            group_id=target.group_id,
        )
        presentation_visible = evidence.dialog_visible and evidence.post_container_visible

    verified = bool(
        target.is_valid
        and route_identity_verified
        and dom_identity_verified
        and trusted_generation_verified
        and evidence.page_guard_passed
        and presentation_visible
    )
    return CommentsNavigationPresentationProof(
        kind=evidence.kind,
        route_identity_verified=route_identity_verified,
        dom_identity_verified=dom_identity_verified,
        trusted_generation_verified=trusted_generation_verified,
        page_guard_passed=evidence.page_guard_passed,
        verified=verified,
    )


def assess_comments_navigation_freshness(
    evidence: CommentsNavigationFreshnessEvidence,
) -> CommentsNavigationFreshnessAssessment:
    """依 bounded runtime facts 判斷 comments snapshot 是否可視為已刷新。"""

    fetch_count = max(int(evidence.facebook_fetch_xhr_completed_count), 0)
    has_generation = bool(evidence.navigation_generation.strip())
    if has_generation and evidence.new_comment_identity_seen:
        outcome = CommentsNavigationFreshnessOutcome.CONFIRMED_NEW_COMMENT
    elif (
        has_generation
        and evidence.trusted_click_completed
        and evidence.post_root_replaced
        and fetch_count > 0
    ):
        outcome = CommentsNavigationFreshnessOutcome.CONFIRMED_REPLACED_ROOT
    elif has_generation and evidence.explicit_refresh_signal_seen:
        outcome = CommentsNavigationFreshnessOutcome.CONFIRMED_UI_SIGNAL
    else:
        outcome = CommentsNavigationFreshnessOutcome.UNCONFIRMED
    return CommentsNavigationFreshnessAssessment(
        outcome=outcome,
        confirmed=outcome != CommentsNavigationFreshnessOutcome.UNCONFIRMED,
        post_root_replaced=evidence.post_root_replaced,
        fetch_xhr_completed_count=fetch_count,
        new_comment_identity_seen=evidence.new_comment_identity_seen,
        explicit_refresh_signal_seen=evidence.explicit_refresh_signal_seen,
    )


def _parse_safe_group_post_href(
    href: str,
) -> tuple[str, str, CommentsNavigationAnchorSource] | None:
    """解析可由 trusted click 使用的窄版 group post href。"""

    text = str(href or "").strip()
    if not text:
        return None
    parsed = urlparse(urljoin(f"{_FACEBOOK_BASE_URL}/", text))
    if not _is_safe_facebook_url(parsed):
        return None
    match = _GROUP_POST_PATH_PATTERN.fullmatch(parsed.path)
    if match is None or not _query_keys_are_safe(parsed.query, _SAFE_POST_QUERY_KEYS):
        return None
    group_id, route_kind, post_id = match.groups()
    if not _SAFE_GROUP_ID_PATTERN.fullmatch(group_id):
        return None
    source = (
        CommentsNavigationAnchorSource.GROUPS_POST
        if route_kind.lower() == "posts"
        else CommentsNavigationAnchorSource.GROUP_PERMALINK
    )
    return group_id, post_id, source


def _matches_safe_group_route(url: str, *, group_id: str) -> bool:
    """判斷 modal 所在 URL 是否仍是預期的安全 group root route。"""

    parsed = urlparse(urljoin(f"{_FACEBOOK_BASE_URL}/", str(url or "").strip()))
    if not _is_safe_facebook_url(parsed):
        return False
    if parsed.path.rstrip("/") != f"/groups/{group_id}":
        return False
    return _query_keys_are_safe(parsed.query, _SAFE_GROUP_QUERY_KEYS)


def _is_safe_facebook_url(parsed: Any) -> bool:
    """限制 trusted navigation identity 只接受 HTTPS Facebook URL。"""

    try:
        port = getattr(parsed, "port", None)
    except ValueError:
        return False
    return bool(
        str(getattr(parsed, "scheme", "") or "").lower() == "https"
        and str(getattr(parsed, "hostname", "") or "").lower() in FACEBOOK_HOSTS
        and getattr(parsed, "username", None) is None
        and getattr(parsed, "password", None) is None
        and port in {None, 443}
        and not str(getattr(parsed, "fragment", "") or "")
    )


def _query_keys_are_safe(query: str, allowed_keys: frozenset[str]) -> bool:
    """只接受已知不改變 post identity 的 query keys。"""

    return all(key in allowed_keys for key, _value in parse_qsl(query, keep_blank_values=True))


def _anchor_source_priority(source: CommentsNavigationAnchorSource) -> int:
    """回傳 navigation anchor source 的穩定優先序。"""

    return {
        CommentsNavigationAnchorSource.GROUPS_POST: 0,
        CommentsNavigationAnchorSource.GROUP_PERMALINK: 1,
    }[source]


__all__ = [
    "CommentsNavigationAnchorCandidate",
    "CommentsNavigationAnchorMatch",
    "CommentsNavigationAnchorSelection",
    "CommentsNavigationAnchorSource",
    "CommentsNavigationFreshnessAssessment",
    "CommentsNavigationFreshnessEvidence",
    "CommentsNavigationFreshnessOutcome",
    "CommentsNavigationOutcome",
    "CommentsNavigationPhase",
    "CommentsNavigationPresentationEvidence",
    "CommentsNavigationPresentationKind",
    "CommentsNavigationPresentationProof",
    "CommentsNavigationResult",
    "CommentsNavigationTargetIdentity",
    "assess_comments_navigation_freshness",
    "match_comments_navigation_anchor",
    "select_comments_navigation_anchor",
    "verify_comments_navigation_presentation",
]
