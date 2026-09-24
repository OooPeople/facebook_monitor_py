"""Shared scan orchestration helpers。

職責：集中 posts/comments pipeline 共用的頁面 guard 與 scan policy 計算。
正式產品主路徑仍是 async resident；sync page guard 只供 debug 與建立 target 工具使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Mapping
from typing import Protocol
from urllib.parse import urlparse

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.scan_failures import CHECKPOINT_REQUIRED_REASON
from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.core.scan_failures import LOGIN_REQUIRED_REASON
from facebook_monitor.core.scan_failures import SESSION_INVALID_REASON
from facebook_monitor.facebook.collection_policy import get_effective_scroll_rounds
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.page_timing import FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS


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
_FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT = r"""
() => {
  const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const boundedText = (element) => clean(element?.innerText || '').slice(0, 800);
  const headingSelector = 'h1, h2, [role="heading"]';
  const headings = Array.from(document.querySelectorAll(headingSelector))
    .map(boundedText)
    .filter(Boolean)
    .slice(0, 24);
  const detailNodes = Array.from(document.querySelectorAll(
    'main p, main span, main div[dir="auto"], [role="main"] p, '
      + '[role="main"] span, [role="main"] div[dir="auto"]'
  ));
  const details = [];
  for (const element of detailNodes) {
    if (element.matches(headingSelector) || element.closest(headingSelector)) continue;
    if (element.querySelector(headingSelector)) continue;
    const text = boundedText(element);
    if (!text || text.length < 8) continue;
    details.push(text);
    if (details.length >= 80) break;
  }
  // 與正式 feed extractor 的 postContainerCandidates 保持相同正常內容邊界。
  const feedCandidateSelector = [
    'a[href*="/groups/"][href*="/posts/"]',
    'a[href*="/groups/"][href*="/post/"]',
    'a[href*="/permalink/"]',
    'a[href*="multi_permalinks="]',
    'a[href*="story_fbid="]',
    'a[href*="set=gm."]',
    '[role="feed"] [role="article"]',
    '[role="feed"] > div',
    'div[data-pagelet*="FeedUnit"]',
    'div[data-pagelet*="GroupsFeed"] [role="article"]',
    '[aria-posinset]',
  ].join(', ');
  const feedRootSelector = [
    '[role="feed"]',
    'div[data-pagelet*="GroupsFeed"]',
    'div[data-pagelet*="FeedUnit"]',
    '[role="main"]',
  ].join(', ');
  const feedCandidates = new Set();
  for (const root of document.querySelectorAll(feedRootSelector)) {
    for (const candidate of root.querySelectorAll(feedCandidateSelector)) {
      feedCandidates.add(candidate);
    }
  }
  return {
    headingTexts: headings,
    detailTexts: details,
    articleCount: document.querySelectorAll('article, [role="article"]').length,
    feedCandidateCount: feedCandidates.size,
  };
}
"""


@dataclass(frozen=True)
class FacebookPageGuardEvidence:
    """保存 page guard 純分類所需、但不直接持久化的頁面證據。"""

    body_text: str
    heading_text: str = ""
    detail_text: str = ""
    current_url: str = ""
    article_count: int | None = None
    feed_candidate_count: int | None = None
    stable_observation_count: int = 0


@dataclass(frozen=True)
class FacebookPageGuardDiagnostics(WorkerFailureDiagnostics):
    """保存可安全寫入 failed scan 的 page guard diagnostics。"""

    classification: str
    facebook_host: bool
    matched_heading: bool
    matched_detail: bool
    article_count: int | None
    stable_observation_count: int
    body_text_length: int
    url_kind: str
    detector_version: int = 1

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
                "article_count": self.article_count,
                "stable_observation_count": self.stable_observation_count,
                "body_text_length": self.body_text_length,
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
    article_count: int | None = None
    feed_candidate_count: int | None = None

    @property
    def signature(self) -> tuple[bool, bool, int | None, int | None]:
        """回傳不含文字的穩定性比較值。"""

        return (
            bool(self.heading_text),
            bool(self.detail_text),
            self.article_count,
            self.feed_candidate_count,
        )


class SyncLocatorLike(Protocol):
    """sync page guard 只需要讀取 locator 文字。"""

    def inner_text(self, *, timeout: int) -> str:
        """讀取 locator 文字內容。"""


class AsyncLocatorLike(Protocol):
    """async page guard 只需要讀取 locator 文字。"""

    async def inner_text(self, *, timeout: int) -> str:
        """讀取 locator 文字內容。"""


class SyncScannablePageLike(Protocol):
    """掃描前 sync guard 需要的最小 Playwright page 能力。

    Facebook sort/extractor helper 仍保留動態 Playwright 邊界；本 Protocol 只描述
    worker pipeline 進入掃描前 guard 會直接使用的能力。
    """

    url: str

    def locator(self, selector: str) -> SyncLocatorLike:
        """回傳指定 selector 的 locator。"""


class AsyncScannablePageLike(Protocol):
    """掃描前 async guard 需要的最小 Playwright page 能力。

    Facebook sort/extractor helper 仍保留動態 Playwright 邊界；本 Protocol 只描述
    worker pipeline 進入掃描前 guard 會直接使用的能力。
    """

    url: str

    def locator(self, selector: str) -> AsyncLocatorLike:
        """回傳指定 selector 的 locator。"""


def classify_facebook_temporary_block(
    evidence: FacebookPageGuardEvidence,
) -> FacebookPageGuardFinding | None:
    """以高可信 DOM 證據分類暫時限制頁或 guard inconclusive。"""

    normalized_body = _normalize_page_text(evidence.body_text)
    if not _is_facebook_url(evidence.current_url):
        return None
    if not _looks_like_temporary_block_body(normalized_body):
        return None
    if evidence.article_count is not None and evidence.article_count > 0:
        return None
    if evidence.feed_candidate_count is not None and evidence.feed_candidate_count > 0:
        return None
    matched_heading = _contains_marker(
        evidence.heading_text,
        _TEMPORARY_BLOCK_TITLE_MARKERS,
    )
    matched_detail = _contains_marker(
        evidence.detail_text,
        _TEMPORARY_BLOCK_DETAIL_MARKERS,
    )
    high_confidence = (
        evidence.article_count == 0
        and evidence.feed_candidate_count == 0
        and evidence.stable_observation_count >= 2
        and matched_heading
        and matched_detail
    )
    reason = (
        FACEBOOK_TEMPORARY_BLOCK_REASON
        if high_confidence
        else FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
    )
    return FacebookPageGuardFinding(
        reason=reason,
        diagnostics=_build_page_guard_diagnostics(
            evidence=evidence,
            classification=reason,
            matched_heading=matched_heading,
            matched_detail=matched_detail,
        ),
    )


def classify_facebook_content_unavailable_evidence(
    evidence: FacebookPageGuardEvidence,
) -> FacebookPageGuardFinding | None:
    """以整頁結構證據分類內容不可見，避免 feed 內局部訊息造成誤判。"""

    normalized_body = _normalize_page_text(evidence.body_text)
    if not _is_facebook_url(evidence.current_url):
        return None
    if not _contains_marker(normalized_body, _CONTENT_UNAVAILABLE_TITLE_MARKERS):
        return None
    if evidence.article_count is not None and evidence.article_count > 0:
        return None
    if evidence.feed_candidate_count is not None and evidence.feed_candidate_count > 0:
        return None
    matched_heading = _contains_marker(
        evidence.heading_text,
        _CONTENT_UNAVAILABLE_TITLE_MARKERS,
    )
    matched_detail = _contains_marker(
        evidence.detail_text,
        _CONTENT_UNAVAILABLE_DETAIL_MARKERS,
    )
    high_confidence = (
        evidence.article_count == 0
        and evidence.feed_candidate_count == 0
        and matched_heading
    )
    reason = (
        CONTENT_UNAVAILABLE_REASON
        if high_confidence
        else FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
    )
    return FacebookPageGuardFinding(
        reason=reason,
        diagnostics=_build_page_guard_diagnostics(
            evidence=evidence,
            classification=reason,
            matched_heading=matched_heading,
            matched_detail=matched_detail,
        ),
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
    """收集 sync bounded structure observation；高風險 guard 可要求二次穩定。"""

    dynamic_page: Any = page
    try:
        first = _normalize_structure_observation(
            dynamic_page.evaluate(_FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT),
            title_markers=title_markers,
            detail_markers=detail_markers,
        )
        if not observe_stability or (
            first.article_count is not None and first.article_count > 0
        ):
            return _evidence_from_observation(
                body_text=body_text,
                current_url=current_url,
                observation=first,
                stable_count=1,
            )
        dynamic_page.wait_for_timeout(FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS)
        second = _normalize_structure_observation(
            dynamic_page.evaluate(_FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT),
            title_markers=title_markers,
            detail_markers=detail_markers,
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
    """收集 async bounded structure observation；高風險 guard 可要求二次穩定。"""

    dynamic_page: Any = page
    try:
        first = _normalize_structure_observation(
            await dynamic_page.evaluate(_FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT),
            title_markers=title_markers,
            detail_markers=detail_markers,
        )
        if not observe_stability or (
            first.article_count is not None and first.article_count > 0
        ):
            return _evidence_from_observation(
                body_text=body_text,
                current_url=current_url,
                observation=first,
                stable_count=1,
            )
        await dynamic_page.wait_for_timeout(FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS)
        second = _normalize_structure_observation(
            await dynamic_page.evaluate(_FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT),
            title_markers=title_markers,
            detail_markers=detail_markers,
        )
    except Exception:
        return FacebookPageGuardEvidence(body_text=body_text, current_url=current_url)
    return _evidence_from_observation(
        body_text=body_text,
        current_url=current_url,
        observation=second,
        stable_count=2 if first.signature == second.signature else 1,
    )


def _normalize_structure_observation(
    raw: object,
    *,
    title_markers: tuple[str, ...],
    detail_markers: tuple[str, ...],
) -> _PageStructureObservation:
    """把 JS payload 轉成 bounded Python observation。"""

    if not isinstance(raw, Mapping):
        return _PageStructureObservation()
    headings = _bounded_text_list(raw.get("headingTexts"), limit=24)
    details = _bounded_text_list(raw.get("detailTexts"), limit=80)
    article_count_raw = raw.get("articleCount")
    article_count = (
        article_count_raw
        if isinstance(article_count_raw, int)
        and not isinstance(article_count_raw, bool)
        and article_count_raw >= 0
        else None
    )
    feed_candidate_count_raw = raw.get("feedCandidateCount")
    feed_candidate_count = (
        feed_candidate_count_raw
        if isinstance(feed_candidate_count_raw, int)
        and not isinstance(feed_candidate_count_raw, bool)
        and feed_candidate_count_raw >= 0
        else None
    )
    return _PageStructureObservation(
        heading_text=_first_matching_text(headings, title_markers),
        detail_text=_first_matching_text(details, detail_markers),
        article_count=article_count,
        feed_candidate_count=feed_candidate_count,
    )


def _bounded_text_list(value: object, *, limit: int) -> tuple[str, ...]:
    """只保留 bounded string list，避免 DOM probe 回傳無界資料。"""

    if not isinstance(value, list):
        return ()
    return tuple(str(item)[:800] for item in value[:limit] if isinstance(item, str))


def _first_matching_text(values: tuple[str, ...], markers: tuple[str, ...]) -> str:
    """回傳第一個命中 marker 的結構文字；只存在 process memory。"""

    return next((value for value in values if _contains_marker(value, markers)), "")


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
        article_count=observation.article_count,
        feed_candidate_count=observation.feed_candidate_count,
        stable_observation_count=stable_count,
    )


def _build_page_guard_diagnostics(
    *,
    evidence: FacebookPageGuardEvidence,
    classification: str,
    matched_heading: bool,
    matched_detail: bool,
) -> FacebookPageGuardDiagnostics:
    """由 raw evidence 建立不含 raw value 的 typed diagnostics。"""

    return FacebookPageGuardDiagnostics(
        classification=classification,
        facebook_host=_is_facebook_url(evidence.current_url),
        matched_heading=matched_heading,
        matched_detail=matched_detail,
        article_count=evidence.article_count,
        stable_observation_count=max(evidence.stable_observation_count, 0),
        body_text_length=len(str(evidence.body_text or "")),
        url_kind=_classify_facebook_url_kind(evidence.current_url),
    )


def _raise_page_guard_failure(finding: FacebookPageGuardFinding) -> None:
    """把 finding 轉成穩定 WorkerFailure，不附 raw page detail。"""

    messages = {
        FACEBOOK_TEMPORARY_BLOCK_REASON: "Facebook temporary access block detected.",
        FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON: ("Facebook page guard evidence is inconclusive."),
        CONTENT_UNAVAILABLE_REASON: ("Facebook 顯示目前無法查看此內容，可能已刪除或權限變更。"),
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


def resolve_effective_scan_scroll_rounds(
    *,
    config: TargetConfig,
    requested_scroll_rounds: int,
) -> int:
    """依 target config 與外部 request 計算實際 scroll rounds。"""

    return get_effective_scroll_rounds(
        target_count=config.max_items_per_scan,
        requested_scroll_rounds=requested_scroll_rounds,
        auto_load_more=config.auto_load_more,
    )
