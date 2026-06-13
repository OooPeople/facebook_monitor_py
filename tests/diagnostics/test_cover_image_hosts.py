"""Cover image host diagnostics tests。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.diagnostics.cover_image_hosts import collect_cover_image_host_report
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.sqlite_connection import SqliteConnection


def test_collect_cover_image_host_report_counts_hosts_and_reject_reasons(
    tmp_path: Path,
) -> None:
    """collector 只統計 hostname / reason，不需要輸出完整 URL。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
            name="private target",
            group_cover_image_url="https://static.facebook.com/images/logos/facebook_2x.png",
        )
        TargetRepository(connection).save(target)
        connection.execute(
            """
            INSERT INTO target_cover_image_refresh_state (
                target_id, status, requested_at, last_attempted_at,
                last_succeeded_at, last_failed_at, last_reported_url,
                last_resolved_url, last_result, changed, error, updated_at
            )
            VALUES (?, 'idle', '', '', '', '',
                    'https://lookaside.fbsbx.com/private.jpg?token=secret',
                    'https://example.com/private.jpg?token=secret',
                    '', 0, '', '2026-05-01T00:00:00+00:00')
            """,
            (target.id,),
        )

        payload = collect_cover_image_host_report(connection)

    assert payload["available"] is True
    overall = cast(dict[str, object], payload["overall"])
    assert overall["value_count"] == 3
    assert overall["accepted_count"] == 1
    assert overall["rejected_count"] == 2
    assert overall["accepted_host_counts"] == {
        "lookaside.fbsbx.com": 1,
    }
    assert overall["accepted_suffix_counts"] == {"fbsbx.com": 1}
    assert overall["reject_reason_counts"] == {
        "generic_facebook_asset": 1,
        "host_not_allowed": 1,
    }
    payload_text = str(payload)
    assert "private-cover.jpg" not in payload_text
    assert "example.com" not in payload_text
    assert "token=secret" not in payload_text
    assert target.id not in payload_text


def test_collect_cover_image_host_report_handles_missing_tables() -> None:
    """缺少正式 table 時回 unavailable，不拋出 sqlite error。"""

    import sqlite3

    connection = sqlite3.connect(":memory:")
    try:
        payload = collect_cover_image_host_report(connection)
    finally:
        connection.close()

    assert payload["available"] is True
    overall = cast(dict[str, object], payload["overall"])
    by_field = cast(dict[str, dict[str, object]], payload["by_field"])
    assert overall["value_count"] == 0
    assert by_field["targets.group_cover_image_url"]["reason"] == "table_missing"
