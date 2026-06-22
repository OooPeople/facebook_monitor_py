"""Support bundle redaction helper tests。"""

from __future__ import annotations

import json
from typing import cast

from facebook_monitor.diagnostics._support_bundle_redaction import _freeform_summary
from facebook_monitor.diagnostics._support_bundle_redaction import _redacted_truncated
from facebook_monitor.diagnostics._support_bundle_redaction import _runtime_diagnostics_text
from facebook_monitor.diagnostics._support_bundle_redaction import _sanitize_metadata
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases


def test_sanitize_metadata_redacts_sensitive_and_unknown_values() -> None:
    """metadata sanitizer 只保留安全 code/count，不輸出 URL、ID 或內文。"""

    payload = _sanitize_metadata(
        {
            "stop_reason": "extractor_failed",
            "postId": "9999999999999999",
            "url": "https://www.facebook.com/groups/private/posts/999",
            "custom private key": "customer secret text",
            "round_count": 3,
            "nested": {"text": "private nested text"},
            "rounds": [
                {"text": "secret item 1"},
                {"text": "secret item 2"},
                {"text": "secret item 3"},
                {"text": "secret item 4"},
            ],
        }
    )
    combined_text = json.dumps(payload, ensure_ascii=False)
    rounds = cast(dict[str, object], payload["rounds"])

    assert payload["stop_reason"] == "extractor_failed"
    assert payload["round_count"] == 3
    assert rounds["count"] == 4
    assert rounds["truncated"] is True
    assert "redacted_key_" in combined_text
    assert "9999999999999999" not in combined_text
    assert "facebook.com" not in combined_text
    assert "customer secret text" not in combined_text
    assert "private nested text" not in combined_text
    assert "secret item" not in combined_text


def test_redacted_truncated_aliases_identifier_assignments() -> None:
    """freeform 文字內常見 identifier assignment 應轉成 bundle-local aliases。"""

    aliases = _SupportBundleAliases()
    text = _redacted_truncated(
        "queued_target_ids=[target-a,target-b] "
        "target_id=target-a source_scan_run_id=123 "
        "notification_event_id=456 dedupe_id=789 "
        "page_id=page-private "
        "https://www.facebook.com/groups/1234567890123456",
        aliases=aliases,
    )

    assert "target_001" in text
    assert "target_002" in text
    assert "scan_run_001" in text
    assert "notification_event_001" in text
    assert "dedupe_001" in text
    assert "page_001" in text
    assert "target-a" not in text
    assert "target-b" not in text
    assert "1234567890123456" not in text
    assert aliases.aliases_by_namespace() == {
        "dedupe": 1,
        "notification_event": 1,
        "page": 1,
        "scan_run": 1,
        "target": 2,
    }


def test_runtime_diagnostics_text_handles_path_value_and_unknown_labels() -> None:
    """runtime diagnostics 應分別處理 path label、安全 value label 與未知 label。"""

    aliases = _SupportBundleAliases()
    sanitized = _runtime_diagnostics_text(
        "Data dir: C:\\Users\\alice\\facebook_monitor_data\n"
        "Version: 1.2.3\n"
        "Private Customer: token=secret target_id=target-private "
        "https://example.test/hook",
        aliases,
    )

    assert "Data dir: [path]" in sanitized
    assert "Version: 1.2.3" in sanitized
    assert "redacted_label_" in sanitized
    assert "target_001" not in sanitized
    assert "target-private" not in sanitized
    assert "alice" not in sanitized
    assert "example.test" not in sanitized
    assert "token=secret" not in sanitized


def test_freeform_summary_detects_url_path_identifier_and_secret_like() -> None:
    """freeform summary 只輸出分類訊號，不輸出原句。"""

    summary = _freeform_summary(
        "failed target_id=target-a "
        "https://discord.com/api/webhooks/123456/private-token "
        "C:\\Users\\alice\\facebook_monitor_data\\app.db"
    )

    assert summary["present"] is True
    assert summary["has_url"] is True
    assert summary["has_path"] is True
    assert summary["has_identifier"] is True
    assert summary["has_secret_like"] is True
    assert summary["redacted"] is True
    assert "target-a" not in json.dumps(summary, ensure_ascii=False)
