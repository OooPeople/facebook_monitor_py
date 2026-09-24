"""Shared scan orchestration tests。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Mapping
from typing import cast

import pytest

from facebook_monitor.worker.failure_diagnostics import (
    serialize_worker_failure_diagnostics,
)
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.facebook_page_guard import classify_facebook_session_failure
from facebook_monitor.worker.facebook_page_guard import (
    classify_facebook_content_unavailable_evidence,
)
from facebook_monitor.worker.facebook_page_guard import classify_facebook_temporary_block
from facebook_monitor.worker.facebook_page_guard import ensure_async_page_scannable
from facebook_monitor.worker.facebook_page_guard import ensure_facebook_login_present
from facebook_monitor.worker.facebook_page_guard import ensure_sync_page_scannable
from facebook_monitor.worker.facebook_page_guard import FacebookPageGuardEvidence


_FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "facebook" / "page_guard"


def _fixture(name: str) -> dict[str, object]:
    """讀取去識別 page guard fixture。"""

    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


class _SyncBodyLocator:
    def __init__(self, body_text: str) -> None:
        self.body_text = body_text

    def inner_text(self, *, timeout: int) -> str:
        assert timeout == 10000
        return self.body_text


class _SyncFixturePage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.url = str(payload["current_url"])
        self.body_text = str(payload["body_text"])
        observations = payload["observations"]
        assert isinstance(observations, list)
        self.observations = cast(list[object], observations)
        self.evaluate_count = 0
        self.wait_count = 0

    def locator(self, selector: str) -> _SyncBodyLocator:
        assert selector == "body"
        return _SyncBodyLocator(self.body_text)

    def evaluate(self, script: str, args: object) -> object:
        assert "matchedHeadingText" in script
        assert "visibleFeedCandidateCount" in script
        assert isinstance(args, dict)
        assert args.get("titleMarkers")
        assert args.get("detailMarkers")
        value = self.observations[min(self.evaluate_count, len(self.observations) - 1)]
        self.evaluate_count += 1
        return value

    def wait_for_timeout(self, timeout: int) -> None:
        assert timeout > 0
        self.wait_count += 1


class _AsyncBodyLocator:
    def __init__(self, body_text: str) -> None:
        self.body_text = body_text

    async def inner_text(self, *, timeout: int) -> str:
        assert timeout == 10000
        return self.body_text


class _AsyncFixturePage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.url = str(payload["current_url"])
        self.body_text = str(payload["body_text"])
        observations = payload["observations"]
        assert isinstance(observations, list)
        self.observations = cast(list[object], observations)
        self.evaluate_count = 0
        self.wait_count = 0

    def locator(self, selector: str) -> _AsyncBodyLocator:
        assert selector == "body"
        return _AsyncBodyLocator(self.body_text)

    async def evaluate(self, script: str, args: object) -> object:
        assert "matchedHeadingText" in script
        assert "visibleFeedCandidateCount" in script
        assert isinstance(args, dict)
        assert args.get("titleMarkers")
        assert args.get("detailMarkers")
        value = self.observations[min(self.evaluate_count, len(self.observations) - 1)]
        self.evaluate_count += 1
        return value

    async def wait_for_timeout(self, timeout: int) -> None:
        assert timeout > 0
        self.wait_count += 1


class _UnsafeDiagnostics(WorkerFailureDiagnostics):
    """模擬企圖攜帶 raw page data 的未知 diagnostics DTO。"""

    def to_safe_mapping(self) -> Mapping[str, object]:
        return {
            "page_guard": {
                "detector": "facebook_scan_page_guard",
                "raw_url": "https://www.facebook.com/groups/private/posts/999",
                "raw_text": "private page body",
            }
        }


def test_classifies_facebook_login_failure_from_url_and_body() -> None:
    """scan guard 會把登入、checkpoint 與 session 過期分類成穩定 reason。"""

    assert (
        classify_facebook_session_failure(
            "Log into Facebook to continue",
            "https://www.facebook.com/login/",
        )
        == "login_required"
    )
    assert (
        classify_facebook_session_failure(
            "Confirm your identity",
            "https://www.facebook.com/checkpoint/123",
        )
        == "checkpoint_required"
    )
    assert classify_facebook_session_failure("Session expired. Please log in again") == (
        "session_invalid"
    )
    assert classify_facebook_session_failure("社團貼文列表", "https://www.facebook.com/") is None


def test_ensure_facebook_login_present_raises_worker_failure_reason() -> None:
    """需要重新登入時保留可被 profile status 判斷的 WorkerFailure reason。"""

    with pytest.raises(WorkerFailure) as exc_info:
        ensure_facebook_login_present(
            "安全檢查",
            "https://www.facebook.com/checkpoint/",
        )

    assert exc_info.value.reason == "checkpoint_required"


@pytest.mark.parametrize(
    ("heading_inside_feed", "visible_count", "heading_text", "expected_reason"),
    [
        (None, 0, "目前無法查看此內容", "facebook_page_guard_inconclusive"),
        (False, 0, "", "facebook_page_guard_inconclusive"),
        (True, 3, "目前無法查看此內容", None),
        (False, 3, "目前無法查看此內容", None),
        (False, 0, "目前無法查看此內容", "content_unavailable"),
    ],
)
def test_content_unavailable_classifier_requires_full_page_structure(
    heading_inside_feed: bool | None,
    visible_count: int,
    heading_text: str,
    expected_reason: str | None,
) -> None:
    """正常 feed 不得因局部訊息誤判；獨立 heading 且無 feed 才能確認。"""

    finding = classify_facebook_content_unavailable_evidence(
        FacebookPageGuardEvidence(
            body_text=("目前無法查看此內容 擁有者變更了分享對象，或是刪除了內容。"),
            heading_text=heading_text,
            detail_text="擁有者變更了分享對象，或是刪除了內容。",
            current_url="https://www.facebook.com/groups/1/",
            heading_inside_feed=heading_inside_feed,
            detail_inside_feed=False,
            heading_detail_local=True,
            visible_feed_candidate_count=visible_count,
            stable_observation_count=1,
        )
    )

    assert (finding.reason if finding else None) == expected_reason
    if finding is not None:
        assert finding.diagnostics is not None


def test_page_guard_allows_normal_feed_with_nested_unavailable_content() -> None:
    """正常 feed 內的已刪除嵌入內容不得被當成整個社團連結失效。"""

    page = _SyncFixturePage(_fixture("quoted_content_unavailable_in_feed.json"))

    ensure_sync_page_scannable(page)

    assert page.evaluate_count == 1
    assert page.wait_count == 0


def test_page_guard_allows_feed_child_without_article_role() -> None:
    """正式 extractor 支援的 feed child 不得被局部 unavailable heading 誤停。"""

    page = _SyncFixturePage(_fixture("quoted_content_unavailable_in_feed_child.json"))

    ensure_sync_page_scannable(page)

    assert page.evaluate_count == 1
    assert page.wait_count == 0


def test_page_guard_confirms_content_unavailable_with_one_structure_probe() -> None:
    """內容不可見每個 page 只探查一次，跨 page 的三次重試另由 policy 負責。"""

    page = _SyncFixturePage(_fixture("content_unavailable_zh_hant.json"))

    with pytest.raises(WorkerFailure) as exc_info:
        ensure_sync_page_scannable(page)

    assert exc_info.value.reason == "content_unavailable"
    assert page.evaluate_count == 1
    assert page.wait_count == 0
    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    payload = serialize_worker_failure_diagnostics(diagnostics).payload
    page_guard = cast(dict[str, object], payload["page_guard"])
    assert page_guard["classification"] == "content_unavailable"
    assert page_guard["heading_inside_feed"] is False
    assert page_guard["visible_feed_candidate_count"] == 0
    assert page_guard["stable_observation_count"] == 1


def test_async_page_guard_confirms_content_unavailable() -> None:
    """正式 async page guard 直接覆蓋內容不可見結構探查。"""

    page = _AsyncFixturePage(_fixture("content_unavailable_zh_hant.json"))

    async def run() -> None:
        await ensure_async_page_scannable(page)

    with pytest.raises(WorkerFailure) as exc_info:
        asyncio.run(run())

    assert exc_info.value.reason == "content_unavailable"
    assert page.evaluate_count == 1
    assert page.wait_count == 0


def test_temporary_block_precedence_ignores_content_unavailable_marker_order() -> None:
    """混合 DOM 必須保留 temporary block 的 pause-all 分類優先權。"""

    observation = {
        "matchedHeadingText": "你暫時遭到封鎖",
        "matchedDetailText": "你似乎過度使用了這項功能，因此暫時無法使用。",
        "headingInsideFeed": False,
        "detailInsideFeed": False,
        "headingDetailLocal": True,
        "visibleFeedCandidateCount": 0,
    }
    page = _SyncFixturePage(
        {
            "body_text": (
                "目前無法查看此內容 擁有者變更了分享對象 你暫時遭到封鎖 你似乎過度使用了這項功能"
            ),
            "current_url": "https://www.facebook.com/groups/111/",
            "observations": [observation, observation],
        }
    )

    with pytest.raises(WorkerFailure) as exc_info:
        ensure_sync_page_scannable(page)

    assert exc_info.value.reason == "facebook_temporary_block"
    assert page.evaluate_count == 2


def test_temporary_block_classifier_requires_high_confidence_structure() -> None:
    """封鎖頁需同時有獨立 heading/detail、零 article 與穩定觀察。"""

    finding = classify_facebook_temporary_block(
        FacebookPageGuardEvidence(
            body_text="你暫時遭到封鎖 你似乎過度使用了這項功能",
            heading_text="你暫時遭到封鎖",
            detail_text="你似乎過度使用了這項功能",
            current_url="https://www.facebook.com/groups/1/permalink/2/",
            heading_inside_feed=False,
            detail_inside_feed=False,
            heading_detail_local=True,
            visible_feed_candidate_count=0,
            stable_observation_count=2,
        )
    )

    assert finding is not None
    assert finding.reason == "facebook_temporary_block"
    assert finding.diagnostics is not None
    serialized = serialize_worker_failure_diagnostics(finding.diagnostics)
    assert serialized.accepted is True
    page_guard = cast(dict[str, object], serialized.payload["page_guard"])
    assert page_guard["url_kind"] == "group_permalink"
    serialized_text = json.dumps(serialized.payload, ensure_ascii=False)
    assert "你暫時遭到封鎖" not in serialized_text
    assert "https://" not in serialized_text


def test_failure_diagnostics_validator_rejects_raw_or_unknown_fields() -> None:
    """未知欄位或 raw page data 不得進入持久化 metadata。"""

    serialized = serialize_worker_failure_diagnostics(_UnsafeDiagnostics())

    assert serialized.accepted is False
    assert serialized.payload == {}
    assert serialized.status == "diagnostics_validation_failed"


@pytest.mark.parametrize(
    (
        "heading_inside_feed",
        "detail_inside_feed",
        "local_relation",
        "visible_count",
        "stable_count",
        "expected_reason",
    ),
    [
        (None, None, None, 0, 0, "facebook_page_guard_inconclusive"),
        (False, False, True, 0, 1, "facebook_page_guard_inconclusive"),
        (True, True, True, 3, 2, None),
        (False, False, False, 2, 2, None),
    ],
)
def test_temporary_block_classifier_handles_inconclusive_and_normal_feed(
    heading_inside_feed: bool | None,
    detail_inside_feed: bool | None,
    local_relation: bool | None,
    visible_count: int,
    stable_count: int,
    expected_reason: str | None,
) -> None:
    """結構不足時 fail closed，但 feed 內 marker 或非局部訊息不得誤判。"""

    finding = classify_facebook_temporary_block(
        FacebookPageGuardEvidence(
            body_text="你暫時遭到封鎖 你似乎過度使用了這項功能",
            heading_text="你暫時遭到封鎖",
            detail_text="你似乎過度使用了這項功能",
            current_url="https://www.facebook.com/groups/1/",
            heading_inside_feed=heading_inside_feed,
            detail_inside_feed=detail_inside_feed,
            heading_detail_local=local_relation,
            visible_feed_candidate_count=visible_count,
            stable_observation_count=stable_count,
        )
    )

    assert (finding.reason if finding else None) == expected_reason


def test_temporary_block_classifier_rejects_lookalike_host() -> None:
    """Facebook 字樣不能讓非 Facebook hostname 通過。"""

    finding = classify_facebook_temporary_block(
        FacebookPageGuardEvidence(
            body_text="You're Temporarily Blocked misusing this feature by going too fast",
            heading_text="You're Temporarily Blocked",
            detail_text="misusing this feature by going too fast",
            current_url="https://evilfacebook.com/groups/1/posts/2",
            heading_inside_feed=False,
            detail_inside_feed=False,
            heading_detail_local=True,
            visible_feed_candidate_count=0,
            stable_observation_count=2,
        )
    )

    assert finding is None


def test_sync_page_guard_uses_sanitized_fixture_and_short_circuits() -> None:
    """sync guard 以兩次穩定 DOM 觀察辨識繁中封鎖頁。"""

    page = _SyncFixturePage(_fixture("temporary_block_zh_hant.json"))

    with pytest.raises(WorkerFailure) as exc_info:
        ensure_sync_page_scannable(page)

    assert exc_info.value.reason == "facebook_temporary_block"
    assert page.evaluate_count == 2
    assert page.wait_count == 1


def test_async_page_guard_normalizes_english_curly_apostrophe() -> None:
    """async guard 與 sync 共用英文/apostrophe 規則與 diagnostics。"""

    page = _AsyncFixturePage(_fixture("temporary_block_en.json"))

    async def run() -> None:
        await ensure_async_page_scannable(page)

    with pytest.raises(WorkerFailure) as exc_info:
        asyncio.run(run())

    assert exc_info.value.reason == "facebook_temporary_block"
    diagnostics = exc_info.value.diagnostics
    assert diagnostics is not None
    payload = serialize_worker_failure_diagnostics(diagnostics).payload
    page_guard = cast(dict[str, object], payload["page_guard"])
    assert page_guard["url_kind"] == "group_post"


def test_page_guard_allows_normal_feed_quoting_block_message() -> None:
    """正常 article feed 引用封鎖訊息時不得停止掃描。"""

    page = _SyncFixturePage(_fixture("quoted_block_message_in_feed.json"))

    ensure_sync_page_scannable(page)

    assert page.evaluate_count == 1
    assert page.wait_count == 0
