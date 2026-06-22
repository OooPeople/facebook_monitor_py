"""Support bundle dedupe collector privacy tests。"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from pathlib import Path
import sqlite3
from typing import cast

from facebook_monitor.diagnostics._support_bundle_dedupe_collectors import (
    _dedupe_summary_payload,
)
from facebook_monitor.diagnostics._support_bundle_redaction import _SupportBundleAliases
from facebook_monitor.persistence.schema import initialize_schema


def test_dedupe_summary_aliases_all_private_identifiers(tmp_path: Path) -> None:
    """dedupe summary 只輸出 alias/count/timestamp，不輸出 raw scope/target ids。"""

    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as connection:
        initialize_schema(connection)
        connection.execute(
            """
            INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
            VALUES (?, ?, ?)
            """,
            ("scope-private-123456789", 1, "2026-05-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO target_dedupe_state (target_id, dedupe_epoch, updated_at)
            VALUES (?, ?, ?)
            """,
            ("target-private-123456789", 7, "2026-05-01T00:00:00+00:00"),
        )
        logical_item_id = connection.execute(
            """
            INSERT INTO logical_items (
                target_id, scope_id, dedupe_epoch, item_kind,
                canonical_item_key, parent_post_id, comment_id,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "target-private-123456789",
                "scope-private-123456789",
                7,
                "post",
                "canonical-private-item-123456789",
                "parent-private-123456789",
                "",
                "2026-05-01T00:00:00+00:00",
                "2026-05-02T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
                "2026-05-02T00:00:00+00:00",
            ),
        ).lastrowid
        connection.execute(
            """
            INSERT INTO logical_item_aliases (
                logical_item_id, target_id, scope_id, dedupe_epoch, alias_key,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                logical_item_id,
                "target-private-123456789",
                "scope-private-123456789",
                7,
                "alias-private-item-123456789",
                "2026-05-01T00:00:00+00:00",
                "2026-05-02T00:00:00+00:00",
                "2026-05-01T00:00:00+00:00",
                "2026-05-02T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO seen_items (
                scope_id, item_key, item_kind, parent_post_id, comment_id,
                first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "scope-private-123456789",
                "seen-private-item-123456789",
                "post",
                "parent-private-123456789",
                "",
                "2026-05-01T00:00:00+00:00",
                "2026-05-02T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO notification_dedupe (
                target_id, dedupe_epoch, event_kind, channel, subject_key,
                logical_item_id, item_key, item_kind, status,
                notification_event_id, failure_reason, failure_count,
                first_queued_at, last_deduped_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "target-private-123456789",
                7,
                "match",
                "discord",
                "subject-private-123456789",
                logical_item_id,
                "notify-private-item-123456789",
                "post",
                "sent",
                987654321,
                "",
                0,
                "2026-05-03T00:00:00+00:00",
                "2026-05-03T00:00:00+00:00",
                "2026-05-03T00:00:00+00:00",
                "2026-05-03T00:00:00+00:00",
            ),
        )

    payload = _dedupe_summary_payload(db_path, _SupportBundleAliases())
    combined_text = json.dumps(payload, ensure_ascii=False)
    scan_scope_state = cast(list[dict[str, object]], payload["scan_scope_state"])
    target_dedupe_state = cast(list[dict[str, object]], payload["target_dedupe_state"])
    logical_item_counts = cast(list[dict[str, object]], payload["logical_item_counts"])
    seen_item_counts = cast(list[dict[str, object]], payload["seen_item_counts"])
    notification_dedupe_counts = cast(
        list[dict[str, object]],
        payload["notification_dedupe_counts"],
    )

    assert payload["available"] is True
    assert scan_scope_state[0]["scope"] == "scope_001"
    assert target_dedupe_state[0]["target"] == "target_001"
    assert logical_item_counts[0]["target"] == "target_001"
    assert logical_item_counts[0]["alias_count_for_target"] == 1
    assert seen_item_counts[0]["scope"] == "scope_001"
    assert notification_dedupe_counts[0]["target"] == "target_001"
    assert "scope-private" not in combined_text
    assert "target-private" not in combined_text
    assert "canonical-private-item" not in combined_text
    assert "alias-private-item" not in combined_text
    assert "seen-private-item" not in combined_text
    assert "notify-private-item" not in combined_text
    assert "subject-private" not in combined_text
    assert "987654321" not in combined_text
    assert "123456789" not in combined_text


def test_dedupe_summary_scan_scope_state_is_bounded_to_latest_100(
    tmp_path: Path,
) -> None:
    """scan scope state 摘要只保留最新 100 筆，避免支援包無界增長。"""

    db_path = tmp_path / "app.db"
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    with sqlite3.connect(db_path) as connection:
        initialize_schema(connection)
        for index in range(101):
            connection.execute(
                """
                INSERT INTO scan_scope_state (scope_id, initialized, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    f"scope-{index:03d}",
                    1,
                    (base + timedelta(seconds=index)).isoformat(),
                ),
            )

    payload = _dedupe_summary_payload(db_path, _SupportBundleAliases())
    scopes = cast(list[dict[str, object]], payload["scan_scope_state"])

    assert len(scopes) == 100
    assert scopes[0]["updated_at"] == (base + timedelta(seconds=100)).isoformat()
    assert all(item["updated_at"] != base.isoformat() for item in scopes)


def test_dedupe_summary_handles_missing_legacy_tables(tmp_path: Path) -> None:
    """舊 DB 缺少 dedupe tables 時，collector 應回傳空 section 而不是失敗。"""

    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    payload = _dedupe_summary_payload(db_path, _SupportBundleAliases())

    assert payload["available"] is True
    assert payload["scan_scope_state"] == []
    assert payload["target_dedupe_state"] == []
    assert payload["logical_item_counts"] == []
    assert payload["seen_item_counts"] == []
    assert payload["notification_dedupe_counts"] == []
