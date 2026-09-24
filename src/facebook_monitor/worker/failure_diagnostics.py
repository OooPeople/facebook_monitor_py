"""Worker failure diagnostics 的型別與安全序列化。

職責：只允許已知 DTO 進入 scan metadata，並在 finalize 前限制欄位、型別、
巢狀深度與總大小，避免 raw page / URL 被任意 mapping 帶入持久化資料。
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
import json
from typing import Final
from typing import Mapping


_MAX_SERIALIZED_BYTES: Final = 2048
_PAGE_GUARD_KEYS: Final = frozenset(
    {
        "detector",
        "detector_version",
        "classification",
        "facebook_host",
        "matched_heading",
        "matched_detail",
        "article_count",
        "stable_observation_count",
        "body_text_length",
        "url_kind",
    }
)
_PAGE_GUARD_CLASSIFICATIONS: Final = frozenset(
    {
        "content_unavailable",
        "facebook_temporary_block",
        "facebook_page_guard_inconclusive",
    }
)
_PAGE_GUARD_URL_KINDS: Final = frozenset(
    {
        "group_permalink",
        "group_post",
        "group_feed",
        "facebook_other",
        "non_facebook",
        "unknown",
    }
)
class WorkerFailureDiagnostics(ABC):
    """可附加到 WorkerFailure 的封閉 diagnostics DTO base。"""

    @abstractmethod
    def to_safe_mapping(self) -> Mapping[str, object]:
        """回傳仍需由 finalize validator 驗證的安全欄位候選。"""


@dataclass(frozen=True)
class SerializedWorkerFailureDiagnostics:
    """保存 diagnostics validator 的結果。"""

    payload: dict[str, object]
    accepted: bool
    status: str = ""


def serialize_worker_failure_diagnostics(
    diagnostics: WorkerFailureDiagnostics | None,
) -> SerializedWorkerFailureDiagnostics:
    """把已知 DTO 轉成 bounded plain mapping；未知或不合法資料一律拒絕。"""

    if diagnostics is None:
        return SerializedWorkerFailureDiagnostics({}, accepted=True)
    if not isinstance(diagnostics, WorkerFailureDiagnostics):
        return _rejected("unsupported_diagnostics_type")
    try:
        raw_payload = diagnostics.to_safe_mapping()
    except Exception:
        return _rejected("diagnostics_serialization_failed")
    payload = validate_serialized_worker_failure_diagnostics(raw_payload)
    if payload is None:
        return _rejected("diagnostics_validation_failed")
    return SerializedWorkerFailureDiagnostics(payload, accepted=True)


def validate_serialized_worker_failure_diagnostics(
    value: object,
) -> dict[str, object] | None:
    """重新驗證 DB/UI 邊界讀到的 diagnostics mapping。"""

    payload = _validate_page_guard_payload(value)
    if payload is None:
        return None
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(serialized) > _MAX_SERIALIZED_BYTES:
        return None
    return payload


def _validate_page_guard_payload(
    value: object,
) -> dict[str, object] | None:
    """驗證第一個固定 variant：`page_guard`。"""

    if not isinstance(value, Mapping) or set(value) != {"page_guard"}:
        return None
    page_guard = value.get("page_guard")
    if not isinstance(page_guard, Mapping) or set(page_guard) != _PAGE_GUARD_KEYS:
        return None
    detector = page_guard.get("detector")
    detector_version = page_guard.get("detector_version")
    classification = page_guard.get("classification")
    facebook_host = page_guard.get("facebook_host")
    matched_heading = page_guard.get("matched_heading")
    matched_detail = page_guard.get("matched_detail")
    article_count = page_guard.get("article_count")
    stable_count = page_guard.get("stable_observation_count")
    body_text_length = page_guard.get("body_text_length")
    url_kind = page_guard.get("url_kind")
    if detector != "facebook_scan_page_guard":
        return None
    if not _is_bounded_int(detector_version, minimum=1, maximum=1000):
        return None
    if classification not in _PAGE_GUARD_CLASSIFICATIONS:
        return None
    if not all(isinstance(item, bool) for item in (facebook_host, matched_heading, matched_detail)):
        return None
    if article_count is not None and not _is_bounded_int(
        article_count,
        minimum=0,
        maximum=100_000,
    ):
        return None
    if not _is_bounded_int(stable_count, minimum=0, maximum=10):
        return None
    if not _is_bounded_int(body_text_length, minimum=0, maximum=10_000_000):
        return None
    if url_kind not in _PAGE_GUARD_URL_KINDS:
        return None
    return {
        "page_guard": {
            "detector": detector,
            "detector_version": detector_version,
            "classification": classification,
            "facebook_host": facebook_host,
            "matched_heading": matched_heading,
            "matched_detail": matched_detail,
            "article_count": article_count,
            "stable_observation_count": stable_count,
            "body_text_length": body_text_length,
            "url_kind": url_kind,
        }
    }


def _is_bounded_int(value: object, *, minimum: int, maximum: int) -> bool:
    """排除 bool，並驗證 bounded integer。"""

    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _rejected(status: str) -> SerializedWorkerFailureDiagnostics:
    """建立不含原資料的拒絕結果。"""

    return SerializedWorkerFailureDiagnostics({}, accepted=False, status=status)


__all__ = [
    "SerializedWorkerFailureDiagnostics",
    "WorkerFailureDiagnostics",
    "serialize_worker_failure_diagnostics",
    "validate_serialized_worker_failure_diagnostics",
]
