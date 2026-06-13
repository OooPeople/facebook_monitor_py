"""Persistence smoke tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.match_history import MatchHistoryRepository
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema


def test_match_history_repository_counts_offsets_and_clears_by_target(
    tmp_path: Path,
) -> None:
    """match history repository 支援 target-scoped 查詢與清空。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        targets = TargetRepository(connection)
        history = MatchHistoryRepository(connection)
        first_target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        second_target = TargetDescriptor.for_group_posts(
            group_id="222",
            canonical_url="https://www.facebook.com/groups/222",
        )
        targets.save(first_target)
        targets.save(second_target)
        for index in range(3):
            history.add(
                MatchHistoryEntry(
                    target_id=first_target.id,
                    group_id=first_target.group_id,
                    item_kind=ItemKind.POST,
                    item_key=f"first-{index}",
                    include_rule="票",
                    text=f"第一個 target 命中 {index}",
                )
            )
        history.add(
            MatchHistoryEntry(
                target_id=second_target.id,
                group_id=second_target.group_id,
                item_kind=ItemKind.POST,
                item_key="second-1",
                include_rule="票",
                text="第二個 target 命中",
            )
        )

        assert history.count_by_target(first_target.id) == 3
        assert history.count_by_target(second_target.id) == 1
        assert [entry.item_key for entry in history.list_by_target(first_target.id, limit=2)] == [
            "first-2",
            "first-1",
        ]
        assert [
            entry.item_key for entry in history.list_by_target(first_target.id, limit=2, offset=1)
        ] == [
            "first-1",
            "first-0",
        ]

        assert history.clear_by_target(first_target.id) == 3
        assert history.count_by_target(first_target.id) == 0
        assert history.count_by_target(second_target.id) == 1


def test_match_history_repository_refreshes_duplicates_and_keeps_target_limit(
    tmp_path: Path,
) -> None:
    """查看紀錄重複 key 刷新到最新，且單一 target 最多保留 10 筆。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        TargetRepository(connection).save(target)
        history = MatchHistoryRepository(connection)
        base_time = utc_now()

        for index in range(12):
            history.add(
                MatchHistoryEntry(
                    target_id=target.id,
                    group_id=target.group_id,
                    item_kind=ItemKind.POST,
                    item_key=f"item-{index}",
                    text=f"命中 {index}",
                    include_rule="票",
                    notified_at=base_time + timedelta(seconds=index),
                    created_at=base_time + timedelta(seconds=index),
                )
            )

        assert history.count_by_target(target.id) == 10
        assert "item-0" not in [entry.item_key for entry in history.list_by_target(target.id)]
        assert "item-1" not in [entry.item_key for entry in history.list_by_target(target.id)]

        history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="item-2",
                text="刷新後的命中",
                include_rule="票",
                notified_at=base_time + timedelta(minutes=1),
                created_at=base_time + timedelta(minutes=1),
            )
        )

        entries = history.list_by_target(target.id)
        assert history.count_by_target(target.id) == 10
        assert [entry for entry in entries if entry.item_key == "item-2"][0].text == "刷新後的命中"
        assert len([entry for entry in entries if entry.item_key == "item-2"]) == 1


def test_match_history_repository_prunes_all_target_limits_independently(
    tmp_path: Path,
) -> None:
    """all-target retention 對每個 target 各自套用上限，不作全域打平。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        targets = TargetRepository(connection)
        history = MatchHistoryRepository(connection)
        base_time = utc_now()
        first_target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        second_target = TargetDescriptor.for_group_posts(
            group_id="222",
            canonical_url="https://www.facebook.com/groups/222",
        )
        targets.save(first_target)
        targets.save(second_target)
        for target in (first_target, second_target):
            for index in range(3):
                history.add(
                    MatchHistoryEntry(
                        target_id=target.id,
                        group_id=target.group_id,
                        item_kind=ItemKind.POST,
                        item_key=f"{target.group_id}-{index}",
                        text=f"命中 {index}",
                        include_rule="票",
                        notified_at=base_time + timedelta(seconds=index),
                        created_at=base_time + timedelta(seconds=index),
                    )
                )

        deleted = history.prune_all_target_limits(limit=2)

        assert deleted == 2
        assert [entry.item_key for entry in history.list_by_target(first_target.id)] == [
            "111-2",
            "111-1",
        ]
        assert [entry.item_key for entry in history.list_by_target(second_target.id)] == [
            "222-2",
            "222-1",
        ]


def test_match_history_repository_preserves_latest_scan_display_order(
    tmp_path: Path,
) -> None:
    """命中紀錄若對到 latest scan snapshot，顯示順序要和最近掃描一致。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        targets = TargetRepository(connection)
        history = MatchHistoryRepository(connection)
        latest_items = LatestScanItemRepository(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        targets.save(target)
        for item_key in ("older", "newer"):
            history.add(
                MatchHistoryEntry(
                    target_id=target.id,
                    group_id=target.group_id,
                    item_kind=ItemKind.POST,
                    item_key=item_key,
                    include_rule="票",
                    text=item_key,
                )
            )
        latest_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="newer",
                    item_index=0,
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="older",
                    item_index=1,
                ),
            ],
        )

        assert [entry.item_key for entry in history.list_by_target(target.id)] == [
            "newer",
            "older",
        ]


def test_match_history_repository_batch_window_matches_single_target_order(
    tmp_path: Path,
) -> None:
    """批次查詢每個 target 的 window 排序要與單 target 查詢一致。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        targets = TargetRepository(connection)
        history = MatchHistoryRepository(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        targets.save(target)
        base_time = utc_now()
        history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="newer",
                include_rule="票",
                text="newer",
                notified_at=base_time + timedelta(seconds=1),
                created_at=base_time + timedelta(seconds=1),
            )
        )
        history.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                item_kind=ItemKind.POST,
                item_key="older",
                include_rule="票",
                text="older",
                notified_at=base_time,
                created_at=base_time,
            )
        )

        single = history.list_by_target(target.id, limit=1)
        batch = history.list_by_targets([target.id], limit_per_target=1)

    assert [entry.item_key for entry in single] == ["newer"]
    assert [entry.item_key for entry in batch[target.id]] == ["newer"]
