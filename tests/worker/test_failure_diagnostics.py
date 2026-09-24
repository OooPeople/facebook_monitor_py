"""Worker failure diagnostics schema 相容性測試。"""

from __future__ import annotations

from copy import deepcopy

from facebook_monitor.worker.failure_diagnostics import (
    validate_serialized_worker_failure_diagnostics,
)


def test_page_guard_diagnostics_v1_persisted_payload_remains_readable() -> None:
    """舊 DB 保存的 detector v1 payload 必須原樣通過 validator。"""

    payload = {
        "page_guard": {
            "detector": "facebook_scan_page_guard",
            "detector_version": 1,
            "classification": "facebook_temporary_block",
            "facebook_host": True,
            "matched_heading": True,
            "matched_detail": True,
            "article_count": 0,
            "stable_observation_count": 2,
            "body_text_length": 48,
            "url_kind": "group_post",
        }
    }

    assert validate_serialized_worker_failure_diagnostics(payload) == payload


def test_page_guard_diagnostics_v2_requires_exact_schema() -> None:
    """v2 只接受完整固定欄位，不容許漏欄或額外 raw data。"""

    payload = {
        "page_guard": {
            "detector": "facebook_scan_page_guard",
            "detector_version": 2,
            "classification": "content_unavailable",
            "facebook_host": True,
            "matched_heading": True,
            "matched_detail": True,
            "heading_inside_feed": False,
            "detail_inside_feed": False,
            "heading_detail_local": True,
            "visible_feed_candidate_count": 0,
            "stable_observation_count": 1,
            "url_kind": "group_feed",
        }
    }

    assert validate_serialized_worker_failure_diagnostics(payload) == payload

    missing_field = deepcopy(payload)
    del missing_field["page_guard"]["heading_detail_local"]
    assert validate_serialized_worker_failure_diagnostics(missing_field) is None

    extra_raw_field = deepcopy(payload)
    extra_raw_field["page_guard"]["raw_url"] = "https://www.facebook.com/groups/private/posts/999"
    assert validate_serialized_worker_failure_diagnostics(extra_raw_field) is None
