"""Persistence smoke tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.app_settings import AppSettingsRepository
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.logical_items import LogicalItemRepository
from facebook_monitor.persistence.repositories.match_history import MatchHistoryRepository
from facebook_monitor.persistence.repositories.notification_dedupe import NotificationDedupeRepository
from facebook_monitor.persistence.repositories.notification_events import NotificationEventRepository
from facebook_monitor.persistence.repositories.scan_runs import ScanRunRepository
from facebook_monitor.persistence.repositories.seen_items import SeenItemRepository
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.repositories.target_runtime_state import TargetRuntimeStateRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.maintenance import RuntimeDataMaintenanceRepository
from facebook_monitor.persistence.repositories.scan_scope_state import ScanScopeStateRepository
from facebook_monitor.persistence.sqlite_codec import encode_datetime

from tests.persistence.sqlite_test_helpers import save_target_config_for_test
from tests.persistence.sqlite_test_helpers import get_target_config_for_test
from tests.persistence.sqlite_test_helpers import notification_outbox_repository
from tests.persistence.sqlite_test_helpers import table_count


def test_runtime_data_maintenance_clears_debug_tables_but_keeps_settings(
    tmp_path: Path,
) -> None:
    """runtime data 清理會刪除可重建資料，但保留 target/config/app settings。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
            group_name="test group",
        )
        TargetRepository(connection).save(target)
        save_target_config_for_test(
            connection,
            target.id,
            TargetConfig(target_id=target.id),
        )
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=target.id,
                desired_state=TargetDesiredState.ACTIVE,
                runtime_status=TargetRuntimeStatus.IDLE,
            )
        )
        AppSettingsRepository(connection).save_theme("dark")
        SeenItemRepository(connection).mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="seen-key", item_kind=ItemKind.POST)
        )
        ScanScopeStateRepository(connection).mark_initialized(target.scope_id)
        scan_id = ScanRunRepository(connection).add(
            ScanRun(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        )
        MatchHistoryRepository(connection).add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="seen-key",
            )
        )
        LatestScanItemRepository(connection).replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-key",
                    item_index=0,
                )
            ],
        )
        NotificationEventRepository(connection).add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-key",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
            )
        )
        notification_outbox_repository(connection).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-key:ntfy",
                target_id=target.id,
                item_key="seen-key",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        result = RuntimeDataMaintenanceRepository(connection).clear_runtime_data(
            include_seen_items=True,
        )

        assert result.scan_runs == 1
        assert result.latest_scan_items == 1
        assert result.match_history == 0
        assert result.notification_events == 1
        assert result.seen_items == 1
        assert result.scan_scope_state == 1
        assert result.notification_outbox == 0
        assert result.total_deleted == 5
        assert table_count(connection, "scan_runs") == 0
        assert table_count(connection, "latest_scan_items") == 0
        assert table_count(connection, "match_history") == 1
        assert table_count(connection, "notification_events") == 0
        assert table_count(connection, "notification_outbox") == 1
        assert table_count(connection, "seen_items") == 0
        assert not ScanScopeStateRepository(connection).is_initialized(target.scope_id)
        assert TargetRepository(connection).get(target.id) is not None
        assert get_target_config_for_test(connection, target.id) is not None
        assert TargetRuntimeStateRepository(connection).get(target.id) is not None
        assert AppSettingsRepository(connection).get_theme() == "dark"


def test_runtime_data_maintenance_keeps_outbox_when_seen_items_are_kept(
    tmp_path: Path,
) -> None:
    """只清 debug tables 時保留 seen/outbox，避免管理清理誤觸通知去重邊界。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        seen_item = SeenItem(
            scope_id=target.scope_id,
            item_key="seen-key",
            item_kind=ItemKind.POST,
        )
        SeenItemRepository(connection).mark_seen(seen_item)
        ScanScopeStateRepository(connection).mark_initialized(target.scope_id)
        logical = LogicalItemRepository(connection).mark_seen_aliases(
            target_id=target.id,
            item=seen_item,
            item_keys=("seen-key", "seen-key-alias"),
        )
        NotificationDedupeRepository(connection).reserve_match(
            target_id=target.id,
            logical_item_id=logical.logical_item_id,
            item_key="seen-key",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        notification_outbox_repository(connection).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:seen-key:ntfy",
                target_id=target.id,
                item_key="seen-key",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        result = RuntimeDataMaintenanceRepository(connection).clear_runtime_data(
            include_seen_items=False,
        )

        assert result.seen_items == 0
        assert result.scan_scope_state == 0
        assert result.notification_outbox == 0
        assert table_count(connection, "seen_items") == 1
        assert table_count(connection, "logical_items") == 1
        assert table_count(connection, "logical_item_aliases") == 2
        assert table_count(connection, "notification_dedupe") == 1
        assert table_count(connection, "notification_outbox") == 1
        assert ScanScopeStateRepository(connection).is_initialized(target.scope_id)


def test_startup_runtime_data_maintenance_preserves_notification_state(
    tmp_path: Path,
) -> None:
    """Web UI 啟動清理只刪 scan/debug snapshot，不碰通知去重狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        seen_item = SeenItem(
            scope_id=target.scope_id,
            item_key="seen-key",
            item_kind=ItemKind.POST,
        )
        SeenItemRepository(connection).mark_seen(seen_item)
        ScanScopeStateRepository(connection).mark_initialized(target.scope_id)
        logical = LogicalItemRepository(connection).mark_seen_aliases(
            target_id=target.id,
            item=seen_item,
            item_keys=("seen-key",),
        )
        NotificationDedupeRepository(connection).reserve_match(
            target_id=target.id,
            logical_item_id=logical.logical_item_id,
            item_key="seen-key",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        scan_id = ScanRunRepository(connection).add(
            ScanRun(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                started_at=utc_now(),
                finished_at=utc_now(),
            )
        )
        LatestScanItemRepository(connection).replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_id,
                    item_kind=ItemKind.POST,
                    item_key="seen-key",
                    item_index=0,
                )
            ],
        )
        NotificationEventRepository(connection).add(
            NotificationEvent(
                target_id=target.id,
                item_key="seen-key",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
            )
        )

        result = RuntimeDataMaintenanceRepository(connection).clear_startup_runtime_data()

        assert result.scan_runs == 1
        assert result.latest_scan_items == 1
        assert result.notification_events == 1
        assert result.seen_items == 0
        assert result.scan_scope_state == 0
        assert table_count(connection, "scan_runs") == 0
        assert table_count(connection, "latest_scan_items") == 0
        assert table_count(connection, "notification_events") == 0
        assert table_count(connection, "seen_items") == 1
        assert table_count(connection, "logical_items") == 1
        assert table_count(connection, "logical_item_aliases") == 1
        assert table_count(connection, "notification_dedupe") == 1
        assert ScanScopeStateRepository(connection).is_initialized(target.scope_id)


def test_runtime_data_maintenance_resets_missing_scope_state_rows(
    tmp_path: Path,
) -> None:
    """清 seen 時會為舊 DB 缺少的 target scope 建 baseline row。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        SeenItemRepository(connection).mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="seen-key", item_kind=ItemKind.POST)
        )

        assert ScanScopeStateRepository(connection).is_initialized(target.scope_id)

        result = RuntimeDataMaintenanceRepository(connection).clear_runtime_data(
            include_seen_items=True,
        )

        assert result.seen_items == 1
        assert result.scan_scope_state == 1
        assert not ScanScopeStateRepository(connection).is_initialized(target.scope_id)


def test_bounded_retention_prunes_terminal_state_but_keeps_recent_failed_outbox(
    tmp_path: Path,
) -> None:
    """bounded retention 清舊 terminal rows，近期 failed outbox 仍保留診斷。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    old = encode_datetime(now - timedelta(days=61))
    recent_failed = encode_datetime(now - timedelta(days=13))
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        logical_repo = LogicalItemRepository(connection)
        sent_logical = logical_repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="sent-item",
                item_kind=ItemKind.POST,
            ),
            item_keys=("sent-item",),
        )
        failed_logical = logical_repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="failed-item",
                item_kind=ItemKind.POST,
            ),
            item_keys=("failed-item",),
        )
        expired_failed_logical = logical_repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="expired-failed-item",
                item_kind=ItemKind.POST,
            ),
            item_keys=("expired-failed-item",),
        )
        SeenItemRepository(connection).mark_seen(
            SeenItem(
                scope_id=target.scope_id,
                item_key="legacy-old",
                item_kind=ItemKind.POST,
            )
        )
        dedupe_repo = NotificationDedupeRepository(connection)
        sent_dedupe = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=sent_logical.logical_item_id,
            item_key="sent-item",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        failed_dedupe = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=failed_logical.logical_item_id,
            item_key="failed-item",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        expired_failed_dedupe = dedupe_repo.reserve_match(
            target_id=target.id,
            logical_item_id=expired_failed_logical.logical_item_id,
            item_key="expired-failed-item",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        outbox_repo = notification_outbox_repository(connection)
        sent_outbox = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:sent-item:ntfy",
                dedupe_id=sent_dedupe.dedupe_id,
                target_id=target.id,
                item_key="sent-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="sent",
                message="sent",
            )
        )
        failed_outbox = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:failed-item:ntfy",
                dedupe_id=failed_dedupe.dedupe_id,
                target_id=target.id,
                item_key="failed-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="failed",
                message="failed",
            )
        )
        expired_failed_outbox = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:expired-failed-item:ntfy",
                dedupe_id=expired_failed_dedupe.dedupe_id,
                target_id=target.id,
                item_key="expired-failed-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="expired failed",
                message="expired failed",
            )
        )
        assert sent_outbox.id is not None
        assert failed_outbox.id is not None
        assert expired_failed_outbox.id is not None
        outbox_repo.mark_result(
            entry_id=sent_outbox.id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
        )
        outbox_repo.mark_result(
            entry_id=failed_outbox.id,
            status=NotificationOutboxStatus.FAILED,
            attempts=1,
            message="failed",
        )
        outbox_repo.mark_result(
            entry_id=expired_failed_outbox.id,
            status=NotificationOutboxStatus.FAILED,
            attempts=1,
            message="expired failed",
        )
        connection.execute(
            "UPDATE notification_outbox SET updated_at = ? WHERE status = 'sent'",
            (old,),
        )
        connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = ?
            WHERE item_key = 'failed-item'
            """,
            (recent_failed,),
        )
        connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = ?
            WHERE item_key = 'expired-failed-item'
            """,
            (old,),
        )
        connection.execute(
            """
            UPDATE notification_dedupe
            SET last_deduped_at = ?, updated_at = ?
            """,
            (old, old),
        )
        connection.execute(
            """
            UPDATE logical_items
            SET last_seen_at = ?, updated_at = ?
            """,
            (old, old),
        )
        connection.execute(
            """
            UPDATE logical_item_aliases
            SET last_seen_at = ?, updated_at = ?
            """,
            (old, old),
        )
        connection.execute(
            """
            UPDATE seen_items
            SET last_seen_at = ?
            """,
            (old,),
        )

        result = RuntimeDataMaintenanceRepository(connection).prune_bounded_retention(
            now=now,
        )
        remaining_outbox_statuses = {
            row["status"]
            for row in connection.execute("SELECT status FROM notification_outbox").fetchall()
        }

    assert result.terminal_outbox == 2
    assert result.notification_dedupe == 2
    assert result.logical_items == 2
    assert result.legacy_seen_items == 1
    assert remaining_outbox_statuses == {"failed"}


def test_bounded_retention_outbox_status_matrix(tmp_path: Path) -> None:
    """bounded retention 依狀態保護 active rows，並套用 terminal TTL。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    old_61_days = encode_datetime(now - timedelta(days=61))
    old_15_days = encode_datetime(now - timedelta(days=15))
    old_13_days = encode_datetime(now - timedelta(days=13))
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        logical_repo = LogicalItemRepository(connection)
        dedupe_repo = NotificationDedupeRepository(connection)
        outbox_repo = notification_outbox_repository(connection)

        def add_outbox_state(
            item_key: str,
            status: NotificationOutboxStatus,
            updated_at: str,
        ) -> None:
            logical = logical_repo.mark_seen_aliases(
                target_id=target.id,
                item=SeenItem(
                    scope_id=target.scope_id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                ),
                item_keys=(item_key,),
            )
            dedupe = dedupe_repo.reserve_match(
                target_id=target.id,
                logical_item_id=logical.logical_item_id,
                item_key=item_key,
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
            )
            outbox_repo.enqueue(
                NotificationOutboxEntry(
                    idempotency_key=f"{target.id}:{item_key}:ntfy",
                    dedupe_id=dedupe.dedupe_id,
                    target_id=target.id,
                    item_key=item_key,
                    item_kind=ItemKind.POST,
                    channel=NotificationChannel.NTFY,
                    title=item_key,
                    message=item_key,
                    status=status,
                )
            )
            connection.execute(
                """
                UPDATE notification_outbox
                SET updated_at = ?
                WHERE item_key = ?
                """,
                (updated_at, item_key),
            )

        add_outbox_state("old-skipped", NotificationOutboxStatus.SKIPPED, old_61_days)
        add_outbox_state(
            "old-processing-failed",
            NotificationOutboxStatus.PROCESSING_FAILED,
            old_15_days,
        )
        add_outbox_state("old-pending", NotificationOutboxStatus.PENDING, old_61_days)
        add_outbox_state(
            "old-processing-pending",
            NotificationOutboxStatus.PROCESSING_PENDING,
            old_61_days,
        )
        add_outbox_state("recent-failed", NotificationOutboxStatus.FAILED, old_13_days)
        add_outbox_state(
            "recent-processing-failed",
            NotificationOutboxStatus.PROCESSING_FAILED,
            old_13_days,
        )
        connection.execute(
            """
            UPDATE notification_dedupe
            SET last_deduped_at = ?, updated_at = ?
            """,
            (old_61_days, old_61_days),
        )
        connection.execute(
            """
            UPDATE logical_items
            SET last_seen_at = ?, updated_at = ?
            """,
            (old_61_days, old_61_days),
        )
        connection.execute(
            """
            UPDATE logical_item_aliases
            SET last_seen_at = ?, updated_at = ?
            """,
            (old_61_days, old_61_days),
        )

        result = RuntimeDataMaintenanceRepository(connection).prune_bounded_retention(
            now=now,
        )
        remaining_item_keys = {
            row["item_key"]
            for row in connection.execute("SELECT item_key FROM notification_outbox").fetchall()
        }

    assert result.terminal_outbox == 2
    assert result.notification_dedupe == 2
    assert result.logical_items == 2
    assert remaining_item_keys == {
        "old-pending",
        "old-processing-pending",
        "recent-failed",
        "recent-processing-failed",
    }


def test_bounded_retention_keeps_latest_scan_logical_item(
    tmp_path: Path,
) -> None:
    """latest scan 仍引用的 logical item 即使過 horizon 也不被清掉。"""

    db_path = tmp_path / "app.db"
    now = utc_now()
    old = encode_datetime(now - timedelta(days=61))
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        logical_repo = LogicalItemRepository(connection)
        kept = logical_repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="latest-item",
                item_kind=ItemKind.POST,
            ),
            item_keys=("latest-item",),
        )
        stale = logical_repo.mark_seen_aliases(
            target_id=target.id,
            item=SeenItem(
                scope_id=target.scope_id,
                item_key="stale-item",
                item_kind=ItemKind.POST,
            ),
            item_keys=("stale-item",),
        )
        LatestScanItemRepository(connection).replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="latest-item",
                    item_index=0,
                )
            ],
        )
        connection.execute(
            """
            UPDATE logical_items
            SET last_seen_at = ?, updated_at = ?
            """,
            (old, old),
        )
        connection.execute(
            """
            UPDATE logical_item_aliases
            SET last_seen_at = ?, updated_at = ?
            """,
            (old, old),
        )

        result = RuntimeDataMaintenanceRepository(connection).prune_bounded_retention(
            now=now,
        )
        remaining_logical_ids = {
            int(row["id"]) for row in connection.execute("SELECT id FROM logical_items").fetchall()
        }

    assert result.logical_items == 1
    assert kept.logical_item_id in remaining_logical_ids
    assert stale.logical_item_id not in remaining_logical_ids
