"""Persistence smoke tests。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import SeenItem
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetDesiredState
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.sidebar_models import SidebarGroup
from facebook_monitor.core.sidebar_models import SidebarTargetPlacement
from facebook_monitor.persistence.repositories.logical_items import LogicalItemRepository
from facebook_monitor.persistence.repositories.notification_dedupe import NotificationDedupeRepository
from facebook_monitor.persistence.repositories.sidebar_layout import SidebarLayoutRepository
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.target_cover_image_refresh import TargetCoverImageRefreshRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.repositories.target_runtime_state import TargetRuntimeStateRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.schema import repair_duplicate_target_scopes

from tests.persistence.sqlite_test_helpers import target_config_repository
from tests.persistence.sqlite_test_helpers import notification_outbox_repository
from tests.persistence.sqlite_test_helpers import PLAINTEXT_SECRET_CODEC


def test_repair_duplicate_target_scopes_preserves_single_row_state(
    tmp_path: Path,
) -> None:
    """duplicate scope repair 會搬移 config/runtime/cover/sidebar，不直接丟資料。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP INDEX idx_targets_kind_scope_unique")
        target_repository = TargetRepository(connection)
        first = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        duplicate = replace(
            first,
            id="duplicate-target",
            name="duplicate",
            created_at=first.created_at + timedelta(seconds=1),
            updated_at=first.updated_at + timedelta(seconds=1),
        )
        target_repository.save(first)
        target_repository.save(duplicate)
        target_config_repository(connection).save_for_target_id(
            duplicate.id,
            TargetConfig(
                target_id=duplicate.id,
                include_keywords=("duplicate-keyword",),
            ),
        )
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=duplicate.id,
                desired_state=TargetDesiredState.ACTIVE,
                runtime_status=TargetRuntimeStatus.QUEUED,
            )
        )
        TargetCoverImageRefreshRepository(connection).request_refresh(
            target_id=duplicate.id,
            reported_url="https://images.example/cover.jpg",
            min_interval_seconds=0,
        )
        sidebar_repository = SidebarLayoutRepository(
            connection,
            secret_codec=PLAINTEXT_SECRET_CODEC,
        )
        group = sidebar_repository.save_group(SidebarGroup.create(name="測試分組", sort_order=0))
        sidebar_repository.save_placement(
            SidebarTargetPlacement(
                target_id=duplicate.id,
                sidebar_group_id=group.id,
                sort_order=3,
            )
        )

        repair_duplicate_target_scopes(connection)

        loaded_config = target_config_repository(connection).get_for_target_id(first.id)
        loaded_runtime = TargetRuntimeStateRepository(connection).get(first.id)
        loaded_cover = TargetCoverImageRefreshRepository(connection).get(first.id)
        placements = SidebarLayoutRepository(
            connection,
            secret_codec=PLAINTEXT_SECRET_CODEC,
        ).list_placements()

    assert loaded_config is not None
    assert loaded_config.include_keywords == ("duplicate-keyword",)
    assert loaded_runtime is not None
    assert loaded_runtime.runtime_status == TargetRuntimeStatus.QUEUED
    assert loaded_cover is not None
    assert loaded_cover.last_reported_url == "https://images.example/cover.jpg"
    assert placements[first.id].sidebar_group_id == group.id
    assert placements[first.id].sort_order == 3
    assert "duplicate-target" not in placements


def test_repair_duplicate_target_scopes_rewrites_notification_outbox_keys(
    tmp_path: Path,
) -> None:
    """duplicate target repair 需同步修正 outbox idempotency key 並合併邏輯重複通知。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP INDEX idx_targets_kind_scope_unique")
        target_repository = TargetRepository(connection)
        first = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        duplicate = replace(
            first,
            id="duplicate-target",
            name="duplicate",
            created_at=first.created_at + timedelta(seconds=1),
            updated_at=first.updated_at + timedelta(seconds=1),
        )
        target_repository.save(first)
        target_repository.save(duplicate)
        outbox = notification_outbox_repository(connection)
        outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{first.id}:same-item:ntfy",
                target_id=first.id,
                item_key="same-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{duplicate.id}:same-item:ntfy",
                target_id=duplicate.id,
                item_key="same-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{duplicate.id}:unique-item:discord",
                target_id=duplicate.id,
                item_key="unique-item",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DISCORD,
                title="title",
                message="message",
            )
        )

        repair_duplicate_target_scopes(connection)

        rows = connection.execute(
            """
            SELECT target_id, item_key, channel, idempotency_key
            FROM notification_outbox
            ORDER BY item_key, channel
            """
        ).fetchall()

    assert [row["target_id"] for row in rows] == [first.id, first.id]
    assert [row["idempotency_key"] for row in rows] == [
        f"{first.id}:same-item:ntfy",
        f"{first.id}:unique-item:discord",
    ]
    assert [row["item_key"] for row in rows] == ["same-item", "unique-item"]


def test_repair_duplicate_target_scopes_merges_conflicting_logical_dedupe(
    tmp_path: Path,
) -> None:
    """duplicate logical dedupe 合併到同一 keep logical item 時不應撞 unique constraint。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("DROP INDEX idx_targets_kind_scope_unique")
        target_repository = TargetRepository(connection)
        first = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        duplicate = replace(
            first,
            id="duplicate-target",
            name="duplicate",
            created_at=first.created_at + timedelta(seconds=1),
            updated_at=first.updated_at + timedelta(seconds=1),
        )
        target_repository.save(first)
        target_repository.save(duplicate)
        logical_repo = LogicalItemRepository(connection)
        keep_logical = logical_repo.mark_seen_aliases(
            target_id=first.id,
            item=SeenItem(
                scope_id=first.scope_id,
                item_key="keep-a",
                item_kind=ItemKind.POST,
            ),
            item_keys=("alias-a", "alias-b"),
        )
        duplicate_logical_a = logical_repo.mark_seen_aliases(
            target_id=duplicate.id,
            item=SeenItem(
                scope_id=duplicate.scope_id,
                item_key="duplicate-a",
                item_kind=ItemKind.POST,
            ),
            item_keys=("alias-a",),
        )
        duplicate_logical_b = logical_repo.mark_seen_aliases(
            target_id=duplicate.id,
            item=SeenItem(
                scope_id=duplicate.scope_id,
                item_key="duplicate-b",
                item_kind=ItemKind.POST,
            ),
            item_keys=("alias-b",),
        )
        dedupe_repo = NotificationDedupeRepository(connection)
        dedupe_a = dedupe_repo.reserve_match(
            target_id=duplicate.id,
            logical_item_id=duplicate_logical_a.logical_item_id,
            item_key="duplicate-a",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        dedupe_b = dedupe_repo.reserve_match(
            target_id=duplicate.id,
            logical_item_id=duplicate_logical_b.logical_item_id,
            item_key="duplicate-b",
            item_kind=ItemKind.POST,
            channel=NotificationChannel.NTFY,
        )
        outbox = notification_outbox_repository(connection)
        outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{duplicate.id}:duplicate-a:ntfy",
                dedupe_id=dedupe_a.dedupe_id,
                target_id=duplicate.id,
                item_key="duplicate-a",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{duplicate.id}:duplicate-b:ntfy",
                dedupe_id=dedupe_b.dedupe_id,
                target_id=duplicate.id,
                item_key="duplicate-b",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

        repair_duplicate_target_scopes(connection)

        dedupe_rows = connection.execute(
            """
            SELECT id, target_id, subject_key
            FROM notification_dedupe
            WHERE subject_key = ?
            ORDER BY id
            """,
            (f"logical:{keep_logical.logical_item_id}",),
        ).fetchall()
        outbox_dedupe_ids = {
            int(row["dedupe_id"])
            for row in connection.execute(
                """
                SELECT dedupe_id
                FROM notification_outbox
                WHERE item_key IN ('duplicate-a', 'duplicate-b')
                """
            ).fetchall()
        }
        remaining_duplicate_logical = connection.execute(
            """
            SELECT COUNT(*)
            FROM logical_items
            WHERE target_id = ?
            """,
            (duplicate.id,),
        ).fetchone()[0]

    assert len(dedupe_rows) == 1
    assert dedupe_rows[0]["target_id"] == first.id
    assert outbox_dedupe_ids == {int(dedupe_rows[0]["id"])}
    assert remaining_duplicate_logical == 0
