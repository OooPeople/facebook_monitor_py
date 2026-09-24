"""Facebook 頁面 guard 的 DOM evidence、分類與 failure adapter。

職責：集中 login/session、temporary-block 與 content-unavailable 的 page-level
判斷；browser-side probe 留在 facebook 層，scan retry 與 incident persistence 留在
既有 worker/application 流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Mapping
from typing import Protocol
from urllib.parse import urlparse

from facebook_monitor.core.scan_failures import CHECKPOINT_REQUIRED_REASON
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import SESSION_INVALID_REASON
from facebook_monitor.facebook.page_guard_script import FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.page_timing import FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS


FACEBOOK_PAGE_GUARD_EVIDENCE_CODE = "facebook_page_guard_v2"
_TEMPORARY_BLOCK_TITLE_MARKERS = (
    "你暫時遭到封鎖",
    "你暂时遭到封锁",
    "you're temporarily blocked",
)
_TEMPORARY_BLOCK_DETAIL_MARKERS = (
    "你似乎過度使用了這項功能",
    "你似乎过度使用了这项功能",
    "misusing this feature by going too fast",
    "temporarily blocked from using it",
    "we limit how often you can post, comment or do other things",
)
_CONTENT_UNAVAILABLE_TITLE_MARKERS = (
    "目前無法查看此內容",
    "目前无法查看此内容",
    "this content isn't available",
    "this content is not available",
    "content isn't available right now",
    "content is not available right now",
)
_CONTENT_UNAVAILABLE_DETAIL_MARKERS = (
    "刪除了內容",
    "删除了内容",
    "變更了分享對象",
    "变更了分享对象",
    "僅與一小群用戶分享",
    "仅与一小群用户分享",
    "shared it with a small group",
    "changed who can see it",
    "deleted",
)


@dataclass(frozen=True)
class FacebookPageGuardEvidence:
    """保存 page guard 純分類所需、但不直接持久化的頁面證據。"""

    body_text: str
    heading_text: str = ""
    detail_text: str = ""
    current_url: str = ""
    heading_inside_feed: bool | None = None
    detail_inside_feed: bool | None = None
    heading_detail_local: bool | None = None
    visible_feed_candidate_count: int | None = None
    stable_observation_count: int = 0


@dataclass(frozen=True)
class FacebookPageGuardDiagnostics(WorkerFailureDiagnostics):
    """保存可安全寫入 failed scan 的 page guard v2 diagnostics。"""

    classification: str
    facebook_host: bool
    matched_heading: bool
    matched_detail: bool
    heading_inside_feed: bool | None
    detail_inside_feed: bool | None
    heading_detail_local: bool | None
    visible_feed_candidate_count: int | None
    stable_observation_count: int
    url_kind: str
    detector_version: int = 2

    def to_safe_mapping(self) -> Mapping[str, object]:
        """輸出固定且不含頁面文字、URL 或 identity 的 mapping。"""

        return {
            "page_guard": {
                "detector": "facebook_scan_page_guard",
                "detector_version": self.detector_version,
                "classification": self.classification,
                "facebook_host": self.facebook_host,
                "matched_heading": self.matched_heading,
                "matched_detail": self.matched_detail,
                "heading_inside_feed": self.heading_inside_feed,
                "detail_inside_feed": self.detail_inside_feed,
                "heading_detail_local": self.heading_detail_local,
                "visible_feed_candidate_count": self.visible_feed_candidate_count,
                "stable_observation_count": self.stable_observation_count,
                "url_kind": self.url_kind,
            }
        }


@dataclass(frozen=True)
class FacebookPageGuardFinding:
    """保存一次 Facebook 掃描頁分類結果。"""

    reason: str
    diagnostics: FacebookPageGuardDiagnostics | None = None


@dataclass(frozen=True)
class _PageStructureObservation:
    """保存單次 bounded DOM 結構觀察。"""

    heading_text: str = ""
    detail_text: str = ""
    heading_inside_feed: bool | None = None
    detail_inside_feed: bool | None = None
    heading_detail_local: bool | None = None
    visible_feed_candidate_count: int | None = None

    @property
    def signature(
        self,
    ) -> tuple[bool, bool, bool | None, bool | None, bool | None, bool | None]:
        """回傳不含文字與易波動精確數量的穩定性比較值。"""

        return (
            bool(self.heading_text),
            bool(self.detail_text),
            self.heading_inside_feed,
            self.detail_inside_feed,
            self.heading_detail_local,
            None
            if self.visible_feed_candidate_count is None
            else self.visible_feed_candidate_count > 0,
        )

    @property
    def marker_inside_feed(self) -> bool:
        """回傳任一命中 marker 是否位於正常 feed item 內。"""

        return self.heading_inside_feed is True or self.detail_inside_feed is True


class SyncLocatorLike(Protocol):
    """sync page guard 只需要讀取 locator 文字。"""

    def inner_text(self, *, timeout: int) -> str:
        """讀取 locator 文字內容。"""


class AsyncLocatorLike(Protocol):
    """async page guard 只需要讀取 locator 文字。"""

    async def inner_text(self, *, timeout: int) -> str:
        """讀取 locator 文字內容。"""


class SyncScannablePageLike(Protocol):
    """掃描前 sync guard 需要的最小 Playwright page 能力。"""

    url: str

    def locator(self, selector: str) -> SyncLocatorLike:
        """回傳指定 selector 的 locator。"""


class AsyncScannablePageLike(Protocol):
    """掃描前 async guard 需要的最小 Playwright page 能力。"""

    url: str

    def locator(self, selector: str) -> AsyncLocatorLike:
        """回傳指定 selector 的 locator。"""


def classify_facebook_temporary_block(
    evidence: FacebookPageGuardEvidence,
) -> FacebookPageGuardFinding | None:
    """以 feed containment 與局部關係分類暫時限制頁。"""

    normalized_body = _normalize_page_text(evidence.body_text)
    if not _is_facebook_url(evidence.current_url):
        return None
    if not _looks_like_temporary_block_body(normalized_body):
        return None
    matched_heading = _contains_marker(
        evidence.heading_text,
        _TEMPORARY_BLOCK_TITLE_MARKERS,
    )
    matched_detail = _contains_marker(
        evidence.detail_text,
        _TEMPORARY_BLOCK_DETAIL_MARKERS,
    )
    if evidence.heading_inside_feed is True or evidence.detail_inside_feed is True:
        return None
    high_confidence = (
        matched_heading
        and matched_detail
        and evidence.heading_inside_feed is False
        and evidence.detail_inside_feed is False
        and evidence.heading_detail_local is True
        and evidence.stable_observation_count >= 2
    )
    if high_confidence:
        return _finding_with_diagnostics(
            reason=FACEBOOK_TEMPORARY_BLOCK_REASON,
            evidence=evidence,
            matched_heading=matched_heading,
            matched_detail=matched_detail,
        )
    if (
        evidence.visible_feed_candidate_count is not None
        and evidence.visible_feed_candidate_count > 0
    ):
        return None
    return _finding_with_diagnostics(
        reason=FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON,
        evidence=evidence,
        matched_heading=matched_heading,
        matched_detail=matched_detail,
    )


def classify_facebook_content_unavailable_evidence(
    evidence: FacebookPageGuardEvidence,
) -> FacebookPageGuardFinding | None:
    """以可見正常內容與 marker containment 分類內容不可見。"""

    normalized_body = _normalize_page_text(evidence.body_text)
    if not _is_facebook_url(evidence.current_url):
        return None
    if not _contains_marker(normalized_body, _CONTENT_UNAVAILABLE_TITLE_MARKERS):
        return None
    matched_heading = _contains_marker(
        evidence.heading_text,
        _CONTENT_UNAVAILABLE_TITLE_MARKERS,
    )
    matched_detail = _contains_marker(
        evidence.detail_text,
        _CONTENT_UNAVAILABLE_DETAIL_MARKERS,
    )
    if evidence.heading_inside_feed is True:
        return None
    if (
        evidence.visible_feed_candidate_count is not None
        and evidence.visible_feed_candidate_count > 0
    ):
        return None
    high_confidence = matched_heading and evidence.heading_inside_feed is False
    reason = (
        CONTENT_UNAVAILABLE_REASON if high_confidence else FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
    )
    return _finding_with_diagnostics(
        reason=reason,
        evidence=evidence,
        matched_heading=matched_heading,
        matched_detail=matched_detail,
    )


def classify_facebook_scan_page_failure(
    evidence: FacebookPageGuardEvidence,
) -> FacebookPageGuardFinding | None:
    """依固定 precedence 分類正式 Facebook scan page failure。"""

    normalized_text = _normalize_page_text(evidence.body_text)
    normalized_url = str(evidence.current_url or "").lower()
    if _looks_like_checkpoint(normalized_text, normalized_url):
        return FacebookPageGuardFinding(CHECKPOINT_REQUIRED_REASON)
    if _looks_like_session_invalid(normalized_text, normalized_url):
        return FacebookPageGuardFinding(SESSION_INVALID_REASON)
    if "/login" in normalized_url:
        return FacebookPageGuardFinding(LOGIN_REQUIRED_REASON)
    block_finding = classify_facebook_temporary_block(evidence)
    if block_finding is not None:
        return block_finding
    if _looks_like_login_page(normalized_text, normalized_url):
        return FacebookPageGuardFinding(LOGIN_REQUIRED_REASON)
    return classify_facebook_content_unavailable_evidence(evidence)


def classify_facebook_session_failure(
    body_text: str,
    current_url: str = "",
) -> str | None:
    """依目前頁面資訊分類 Facebook session 失效原因。"""

    normalized_text = body_text.lower()
    normalized_url = current_url.lower()
    if _looks_like_checkpoint(normalized_text, normalized_url):
        return CHECKPOINT_REQUIRED_REASON
    if _looks_like_session_invalid(normalized_text, normalized_url):
        return SESSION_INVALID_REASON
    if _looks_like_login_page(normalized_text, normalized_url):
        return LOGIN_REQUIRED_REASON
    return None


def ensure_facebook_login_present(body_text: str, current_url: str = "") -> None:
    """檢查頁面是否要求登入；需要登入時拋出 worker failure。"""

    reason = classify_facebook_session_failure(body_text, current_url)
    if reason:
        raise WorkerFailure(reason, "Facebook login is required.")


def ensure_sync_page_logged_in(page: SyncScannablePageLike) -> None:
    """sync Playwright page 登入 guard。"""

    ensure_facebook_login_present(
        page.locator("body").inner_text(timeout=10000),
        str(getattr(page, "url", "") or ""),
    )


def ensure_sync_page_scannable(page: SyncScannablePageLike) -> None:
    """sync Playwright page 掃描前 guard。"""

    finding = assess_sync_facebook_page(page)
    if finding is not None:
        _raise_page_guard_failure(finding)


def assess_sync_facebook_page(
    page: SyncScannablePageLike,
) -> FacebookPageGuardFinding | None:
    """評估 sync Facebook document，供 scan 與非 scan source 共用 typed signal。"""

    body_text = page.locator("body").inner_text(timeout=10000)
    current_url = str(getattr(page, "url", "") or "")
    evidence = FacebookPageGuardEvidence(body_text=body_text, current_url=current_url)
    normalized_body = _normalize_page_text(body_text)
    if _looks_like_temporary_block_body(normalized_body):
        evidence = _collect_sync_page_guard_evidence(
            page=page,
            body_text=body_text,
            current_url=current_url,
            title_markers=_TEMPORARY_BLOCK_TITLE_MARKERS,
            detail_markers=_TEMPORARY_BLOCK_DETAIL_MARKERS,
            observe_stability=True,
        )
    elif _contains_marker(normalized_body, _CONTENT_UNAVAILABLE_TITLE_MARKERS):
        evidence = _collect_sync_page_guard_evidence(
            page=page,
            body_text=body_text,
            current_url=current_url,
            title_markers=_CONTENT_UNAVAILABLE_TITLE_MARKERS,
            detail_markers=_CONTENT_UNAVAILABLE_DETAIL_MARKERS,
            observe_stability=False,
        )
    return classify_facebook_scan_page_failure(evidence)


async def ensure_async_page_logged_in(page: AsyncScannablePageLike) -> None:
    """async Playwright page 登入 guard。"""

    body_text = await page.locator("body").inner_text(timeout=10000)
    ensure_facebook_login_present(body_text, str(getattr(page, "url", "") or ""))


async def ensure_async_page_scannable(page: AsyncScannablePageLike) -> None:
    """async Playwright page 掃描前 guard。"""

    finding = await assess_async_facebook_page(page)
    if finding is not None:
        _raise_page_guard_failure(finding)


async def assess_async_facebook_page(
    page: AsyncScannablePageLike,
) -> FacebookPageGuardFinding | None:
    """評估 async Facebook document，供 scan 與非 scan source 共用 typed signal。"""

    body_text = await page.locator("body").inner_text(timeout=10000)
    current_url = str(getattr(page, "url", "") or "")
    evidence = FacebookPageGuardEvidence(body_text=body_text, current_url=current_url)
    normalized_body = _normalize_page_text(body_text)
    if _looks_like_temporary_block_body(normalized_body):
        evidence = await _collect_async_page_guard_evidence(
            page=page,
            body_text=body_text,
            current_url=current_url,
            title_markers=_TEMPORARY_BLOCK_TITLE_MARKERS,
            detail_markers=_TEMPORARY_BLOCK_DETAIL_MARKERS,
            observe_stability=True,
        )
    elif _contains_marker(normalized_body, _CONTENT_UNAVAILABLE_TITLE_MARKERS):
        evidence = await _collect_async_page_guard_evidence(
            page=page,
            body_text=body_text,
            current_url=current_url,
            title_markers=_CONTENT_UNAVAILABLE_TITLE_MARKERS,
            detail_markers=_CONTENT_UNAVAILABLE_DETAIL_MARKERS,
            observe_stability=False,
        )
    return classify_facebook_scan_page_failure(evidence)


def _collect_sync_page_guard_evidence(
    *,
    page: SyncScannablePageLike,
    body_text: str,
    current_url: str,
    title_markers: tuple[str, ...],
    detail_markers: tuple[str, ...],
    observe_stability: bool,
) -> FacebookPageGuardEvidence:
    """收集 sync bounded marker context；高風險 guard 可要求二次穩定。"""

    dynamic_page: Any = page
    probe_args = {"titleMarkers": title_markers, "detailMarkers": detail_markers}
    try:
        first = _normalize_structure_observation(
            dynamic_page.evaluate(FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT, probe_args)
        )
        if not observe_stability or first.marker_inside_feed:
            return _evidence_from_observation(
                body_text=body_text,
                current_url=current_url,
                observation=first,
                stable_count=1,
            )
        dynamic_page.wait_for_timeout(FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS)
        second = _normalize_structure_observation(
            dynamic_page.evaluate(FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT, probe_args)
        )
    except Exception:
        return FacebookPageGuardEvidence(body_text=body_text, current_url=current_url)
    return _evidence_from_observation(
        body_text=body_text,
        current_url=current_url,
        observation=second,
        stable_count=2 if first.signature == second.signature else 1,
    )


async def _collect_async_page_guard_evidence(
    *,
    page: AsyncScannablePageLike,
    body_text: str,
    current_url: str,
    title_markers: tuple[str, ...],
    detail_markers: tuple[str, ...],
    observe_stability: bool,
) -> FacebookPageGuardEvidence:
    """收集 async bounded marker context；高風險 guard 可要求二次穩定。"""

    dynamic_page: Any = page
    probe_args = {"titleMarkers": title_markers, "detailMarkers": detail_markers}
    try:
        first = _normalize_structure_observation(
            await dynamic_page.evaluate(FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT, probe_args)
        )
        if not observe_stability or first.marker_inside_feed:
            return _evidence_from_observation(
                body_text=body_text,
                current_url=current_url,
                observation=first,
                stable_count=1,
            )
        await dynamic_page.wait_for_timeout(FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS)
        second = _normalize_structure_observation(
            await dynamic_page.evaluate(FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT, probe_args)
        )
    except Exception:
        return FacebookPageGuardEvidence(body_text=body_text, current_url=current_url)
    return _evidence_from_observation(
        body_text=body_text,
        current_url=current_url,
        observation=second,
        stable_count=2 if first.signature == second.signature else 1,
    )


def _normalize_structure_observation(raw: object) -> _PageStructureObservation:
    """把 JS payload 轉成 bounded Python observation。"""

    if not isinstance(raw, Mapping):
        return _PageStructureObservation()
    visible_count_raw = raw.get("visibleFeedCandidateCount")
    visible_count = (
        visible_count_raw
        if isinstance(visible_count_raw, int)
        and not isinstance(visible_count_raw, bool)
        and visible_count_raw >= 0
        else None
    )
    return _PageStructureObservation(
        heading_text=_bounded_text(raw.get("matchedHeadingText")),
        detail_text=_bounded_text(raw.get("matchedDetailText")),
        heading_inside_feed=_nullable_bool(raw.get("headingInsideFeed")),
        detail_inside_feed=_nullable_bool(raw.get("detailInsideFeed")),
        heading_detail_local=_nullable_bool(raw.get("headingDetailLocal")),
        visible_feed_candidate_count=visible_count,
    )


def _bounded_text(value: object) -> str:
    """只保留單一 bounded string，避免 DOM probe 回傳無界資料。"""

    return str(value)[:800] if isinstance(value, str) else ""


def _nullable_bool(value: object) -> bool | None:
    """只接受 DOM probe 的 bool/null tri-state。"""

    return value if isinstance(value, bool) else None


def _evidence_from_observation(
    *,
    body_text: str,
    current_url: str,
    observation: _PageStructureObservation,
    stable_count: int,
) -> FacebookPageGuardEvidence:
    """合成純 classifier evidence。"""

    return FacebookPageGuardEvidence(
        body_text=body_text,
        heading_text=observation.heading_text,
        detail_text=observation.detail_text,
        current_url=current_url,
        heading_inside_feed=observation.heading_inside_feed,
        detail_inside_feed=observation.detail_inside_feed,
        heading_detail_local=observation.heading_detail_local,
        visible_feed_candidate_count=observation.visible_feed_candidate_count,
        stable_observation_count=stable_count,
    )


def _finding_with_diagnostics(
    *,
    reason: str,
    evidence: FacebookPageGuardEvidence,
    matched_heading: bool,
    matched_detail: bool,
) -> FacebookPageGuardFinding:
    """由分類結果建立不含 raw value 的 typed diagnostics。"""

    return FacebookPageGuardFinding(
        reason=reason,
        diagnostics=FacebookPageGuardDiagnostics(
            classification=reason,
            facebook_host=_is_facebook_url(evidence.current_url),
            matched_heading=matched_heading,
            matched_detail=matched_detail,
            heading_inside_feed=evidence.heading_inside_feed,
            detail_inside_feed=evidence.detail_inside_feed,
            heading_detail_local=evidence.heading_detail_local,
            visible_feed_candidate_count=evidence.visible_feed_candidate_count,
            stable_observation_count=max(evidence.stable_observation_count, 0),
            url_kind=_classify_facebook_url_kind(evidence.current_url),
        ),
    )


def _raise_page_guard_failure(finding: FacebookPageGuardFinding) -> None:
    """把 finding 轉成穩定 WorkerFailure，不附 raw page detail。"""

    messages = {
        FACEBOOK_TEMPORARY_BLOCK_REASON: "Facebook temporary access block detected.",
        FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON: "Facebook page guard evidence is inconclusive.",
        CONTENT_UNAVAILABLE_REASON: "Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。",
    }
    raise WorkerFailure(
        finding.reason,
        messages.get(finding.reason, "Facebook login is required."),
        diagnostics=finding.diagnostics,
    )


def _normalize_page_text(value: str) -> str:
    """正規化 page marker 比對文字。"""

    return " ".join(str(value or "").replace("’", "'").lower().split())


def _contains_marker(value: str, markers: tuple[str, ...]) -> bool:
    """判斷正規化文字是否包含任一窄 marker。"""

    normalized = _normalize_page_text(value)
    return any(marker in normalized for marker in markers)


def _looks_like_temporary_block_body(normalized_text: str) -> bool:
    """低成本判斷 body 是否值得做結構探查。"""

    return _contains_marker(
        normalized_text,
        _TEMPORARY_BLOCK_TITLE_MARKERS,
    ) and _contains_marker(normalized_text, _TEMPORARY_BLOCK_DETAIL_MARKERS)


def _is_facebook_url(value: str) -> bool:
    """嚴格判斷 URL hostname 是否為 Facebook。"""

    try:
        hostname = (urlparse(str(value or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return hostname == "facebook.com" or hostname.endswith(".facebook.com")


def _classify_facebook_url_kind(value: str) -> str:
    """把 Facebook route 分成不含 identity 的 enum。"""

    if not str(value or "").strip():
        return "unknown"
    if not _is_facebook_url(value):
        return "non_facebook"
    try:
        segments = [item for item in urlparse(value).path.lower().split("/") if item]
    except ValueError:
        return "unknown"
    if len(segments) >= 4 and segments[0] == "groups" and segments[2] == "permalink":
        return "group_permalink"
    if len(segments) >= 4 and segments[0] == "groups" and segments[2] == "posts":
        return "group_post"
    if len(segments) >= 2 and segments[0] == "groups":
        return "group_feed"
    return "facebook_other"


def _looks_like_login_page(normalized_text: str, normalized_url: str) -> bool:
    """判斷 Facebook 是否落在登入頁或登入提示。"""

    if "/login" in normalized_url:
        return True
    login_markers = (
        "log into facebook",
        "log in to facebook",
        "登入 facebook",
        "登入你的 facebook",
    )
    return any(marker in normalized_text for marker in login_markers)


def _looks_like_checkpoint(normalized_text: str, normalized_url: str) -> bool:
    """判斷 Facebook 是否要求 checkpoint / 安全驗證。"""

    if "/checkpoint" in normalized_url:
        return True
    checkpoint_markers = (
        "checkpoint",
        "security check",
        "confirm your identity",
        "確認你的身分",
        "確認你的身份",
        "安全檢查",
    )
    return any(marker in normalized_text for marker in checkpoint_markers)


def _looks_like_session_invalid(normalized_text: str, normalized_url: str) -> bool:
    """判斷 Facebook 是否顯示 session 已過期。"""

    if "/recover" in normalized_url:
        return True
    session_markers = (
        "session expired",
        "please log in again",
        "請重新登入",
        "工作階段已過期",
    )
    return any(marker in normalized_text for marker in session_markers)


__all__ = [
    "AsyncScannablePageLike",
    "FACEBOOK_PAGE_GUARD_EVIDENCE_CODE",
    "FacebookPageGuardDiagnostics",
    "FacebookPageGuardEvidence",
    "FacebookPageGuardFinding",
    "SyncScannablePageLike",
    "assess_async_facebook_page",
    "assess_sync_facebook_page",
    "classify_facebook_content_unavailable_evidence",
    "classify_facebook_scan_page_failure",
    "classify_facebook_session_failure",
    "classify_facebook_temporary_block",
    "ensure_async_page_logged_in",
    "ensure_async_page_scannable",
    "ensure_facebook_login_present",
    "ensure_sync_page_logged_in",
    "ensure_sync_page_scannable",
]
