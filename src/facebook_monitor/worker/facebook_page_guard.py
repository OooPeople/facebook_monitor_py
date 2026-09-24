"""Facebook 頁面 guard 的 browser/runtime adapter 與相容 façade。

職責：收集 bounded DOM evidence 並轉成 WorkerFailure；純分類與 diagnostics 組裝
位於 `worker.facebook_page_guard_classification`，既有 public imports 由本模組維持。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Mapping
from typing import Protocol

from facebook_monitor.core.scan_failures import CONTENT_UNAVAILABLE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_PAGE_GUARD_INCONCLUSIVE_REASON
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.facebook.page_guard_script import FACEBOOK_PAGE_GUARD_STRUCTURE_SCRIPT
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_page_guard_classification import (
    _CONTENT_UNAVAILABLE_DETAIL_MARKERS,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    _CONTENT_UNAVAILABLE_TITLE_MARKERS,
)
from facebook_monitor.worker.facebook_page_guard_classification import _contains_marker
from facebook_monitor.worker.facebook_page_guard_classification import (
    _looks_like_temporary_block_body,
)
from facebook_monitor.worker.facebook_page_guard_classification import _normalize_page_text
from facebook_monitor.worker.facebook_page_guard_classification import (
    _TEMPORARY_BLOCK_DETAIL_MARKERS,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    _TEMPORARY_BLOCK_TITLE_MARKERS,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    classify_facebook_content_unavailable_evidence,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    classify_facebook_scan_page_failure,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    classify_facebook_session_failure,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    classify_facebook_temporary_block,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    FACEBOOK_PAGE_GUARD_EVIDENCE_CODE,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    FacebookPageGuardDiagnostics,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    FacebookPageGuardEvidence,
)
from facebook_monitor.worker.facebook_page_guard_classification import (
    FacebookPageGuardFinding,
)
from facebook_monitor.worker.page_timing import FACEBOOK_PAGE_GUARD_STABLE_WAIT_MS


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
