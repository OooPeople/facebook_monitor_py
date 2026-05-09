"""Persistence smoke tests。"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import GlobalNotificationSettings
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
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.persistence.sqlite import MatchHistoryRepository
from facebook_monitor.persistence.sqlite import GlobalNotificationSettingsRepository
from facebook_monitor.persistence.sqlite import LatestScanItemRepository
from facebook_monitor.persistence.sqlite import NotificationEventRepository
from facebook_monitor.persistence.sqlite import NotificationOutboxRepository
from facebook_monitor.persistence.sqlite import ScanRunRepository
from facebook_monitor.persistence.sqlite import SeenItemRepository
from facebook_monitor.persistence.sqlite import SqliteConnection
from facebook_monitor.persistence.sqlite import TargetConfigRepository
from facebook_monitor.persistence.sqlite import TargetRepository
from facebook_monitor.persistence.sqlite import TargetRuntimeStateRepository
from facebook_monitor.persistence.sqlite import initialize_schema
from facebook_monitor.persistence.maintenance import RuntimeDataMaintenanceRepository


def table_count(connection: object, table_name: str) -> int:
    """回傳指定測試資料表目前筆數。"""

    row = connection.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
    return int(row[0])


def test_sqlite_connection_uses_wal_busy_timeout_and_dashboard_indexes(tmp_path: Path) -> None:
    """SQLite 連線與 schema 具備 Web UI/background worker 並行所需設定。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 5000
    assert int(synchronous) == 1
    assert {
        "idx_targets_kind_scope",
        "idx_scan_runs_target_created",
        "idx_notification_events_target_created",
        "idx_latest_scan_items_target_index",
        "idx_runtime_state_status_updated",
        "idx_runtime_state_desired_updated",
    }.issubset(indexes)


def test_initialize_schema_migrates_legacy_paused_runtime_status(tmp_path: Path) -> None:
    """舊 DB 的 runtime_status=paused 會升級成 idle，paused 語義只保留在 target flag。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        target = TargetDescriptor.for_group_posts(
            group_id="111",
            canonical_url="https://www.facebook.com/groups/111",
        )
        TargetRepository(connection).save(target)
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=target.id,
                desired_state=TargetDesiredState.STOPPED,
                runtime_status=TargetRuntimeStatus.IDLE,
            )
        )
        connection.execute(
            "UPDATE schema_metadata SET value = '11' WHERE key = 'version'"
        )
        connection.execute(
            """
            UPDATE target_runtime_state
            SET runtime_status = 'paused'
            WHERE target_id = ?
            """,
            (target.id,),
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        loaded = TargetRuntimeStateRepository(connection).get(target.id)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]

    assert loaded is not None
    assert loaded.runtime_status == TargetRuntimeStatus.IDLE
    assert loaded.desired_state == TargetDesiredState.STOPPED
    assert version == "12"


def test_initialize_schema_migrates_v10_fixture_to_current_schema(tmp_path: Path) -> None:
    """raw v10 DB 會升級 group config、runtime、history 與 latest scan 資料。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        create_raw_v10_fixture_schema(connection)

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'version'"
        ).fetchone()["value"]
        posts_target = TargetRepository(connection).get("posts-target")
        comments_target = TargetRepository(connection).get("comments-target")
        group_config = TargetConfigRepository(connection).get_for_group("111")
        runtime_state = TargetRuntimeStateRepository(connection).get("posts-target")
        latest_scan = ScanRunRepository(connection).latest_by_target("posts-target")
        latest_items = LatestScanItemRepository(connection).list_by_target("posts-target")
        notifications = NotificationEventRepository(connection).list_by_target("posts-target")
        history = MatchHistoryRepository(connection).list_by_target("posts-target")
        has_seen = SeenItemRepository(connection).has_seen("111", "legacy-item")

    assert version == "12"
    assert posts_target is not None
    assert posts_target.group_id == "111"
    assert comments_target is not None
    assert comments_target.parent_post_id == "999"
    assert group_config is not None
    assert group_config.group_id == "111"
    assert group_config.include_keywords == ("legacy",)
    assert group_config.fixed_refresh_sec == 90
    assert group_config.enable_desktop_notification is False
    assert group_config.enable_ntfy
    assert group_config.ntfy_topic == "legacy-topic"
    assert group_config.enable_discord_notification is False
    assert group_config.discord_webhook == ""
    assert runtime_state is not None
    assert runtime_state.runtime_status == TargetRuntimeStatus.IDLE
    assert runtime_state.desired_state == TargetDesiredState.STOPPED
    assert runtime_state.scan_requested_at is None
    assert runtime_state.last_skip_reason == ""
    assert latest_scan is not None
    assert latest_scan.item_count == 1
    assert [item.item_key for item in latest_items] == ["legacy-item"]
    assert latest_items[0].debug_metadata == {}
    assert len(notifications) == 1
    assert notifications[0].message == "legacy sent"
    assert len(history) == 1
    assert history[0].include_rule == "legacy"
    assert has_seen


def create_raw_v10_fixture_schema(connection: object) -> None:
    """建立不經 current schema helper 的 v10 代表性舊 DB。"""

    connection.executescript(
        """
        CREATE TABLE schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO schema_metadata (key, value) VALUES ('version', '10');

        CREATE TABLE targets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            paused INTEGER NOT NULL,
            worker_mode TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE target_configs (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            include_keywords TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
            min_refresh_sec INTEGER NOT NULL,
            max_refresh_sec INTEGER NOT NULL,
            jitter_enabled INTEGER NOT NULL,
            fixed_refresh_sec INTEGER,
            max_items_per_scan INTEGER NOT NULL,
            auto_load_more INTEGER NOT NULL,
            auto_adjust_sort INTEGER NOT NULL,
            enable_ntfy INTEGER NOT NULL,
            ntfy_topic TEXT NOT NULL
        );

        CREATE TABLE seen_items (
            scope_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (scope_id, item_key)
        );

        CREATE TABLE match_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            group_id TEXT NOT NULL,
            group_name TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            parent_post_id TEXT NOT NULL,
            comment_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            permalink TEXT NOT NULL,
            include_rule TEXT NOT NULL,
            timestamp_text TEXT NOT NULL,
            notified_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE latest_scan_items (
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            scan_run_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_key TEXT NOT NULL,
            item_index INTEGER NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            permalink TEXT NOT NULL,
            matched_keyword TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            PRIMARY KEY (target_id, item_key)
        );

        CREATE TABLE scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            item_count INTEGER NOT NULL,
            matched_count INTEGER NOT NULL,
            error_message TEXT NOT NULL,
            worker_mode TEXT NOT NULL,
            metadata TEXT NOT NULL
        );

        CREATE TABLE notification_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE target_runtime_state (
            target_id TEXT PRIMARY KEY REFERENCES targets(id) ON DELETE CASCADE,
            desired_state TEXT NOT NULL,
            runtime_status TEXT NOT NULL,
            last_heartbeat_at TEXT NOT NULL,
            last_error TEXT NOT NULL,
            active_worker_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    now = "2026-01-01T00:00:00+00:00"
    connection.execute(
        """
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, parent_post_id,
            scope_id, canonical_url, enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            "legacy group",
            "posts",
            "111",
            "legacy group",
            "",
            "111",
            "https://www.facebook.com/groups/111",
            1,
            0,
            "headless",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO targets (
            id, name, target_kind, group_id, group_name, parent_post_id,
            scope_id, canonical_url, enabled, paused, worker_mode, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "comments-target",
            "legacy comments",
            "comments",
            "111",
            "legacy group",
            "999",
            "111:post:999:comments",
            "https://www.facebook.com/groups/111/posts/999",
            1,
            1,
            "headless",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO target_configs (
            target_id, include_keywords, exclude_keywords, min_refresh_sec,
            max_refresh_sec, jitter_enabled, fixed_refresh_sec, max_items_per_scan,
            auto_load_more, auto_adjust_sort, enable_ntfy, ntfy_topic
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            '["legacy"]',
            "[]",
            30,
            120,
            0,
            90,
            7,
            1,
            1,
            1,
            "legacy-topic",
        ),
    )
    connection.execute(
        """
        INSERT INTO target_runtime_state (
            target_id, desired_state, runtime_status, last_heartbeat_at,
            last_error, active_worker_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("posts-target", "stopped", "paused", now, "", "", now),
    )
    connection.execute(
        """
        INSERT INTO scan_runs (
            id, target_id, started_at, finished_at, status, item_count,
            matched_count, error_message, worker_mode, metadata
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "posts-target", now, now, "success", 1, 1, "", "headless", "{}"),
    )
    connection.execute(
        """
        INSERT INTO latest_scan_items (
            target_id, scan_run_id, item_kind, item_key, item_index,
            author, text, permalink, matched_keyword, scanned_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            1,
            "post",
            "legacy-item",
            0,
            "author",
            "legacy text",
            "https://www.facebook.com/groups/111/posts/1",
            "legacy",
            now,
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
        ("111", "legacy-item", "post", "", "", now, now),
    )
    connection.execute(
        """
        INSERT INTO notification_events (
            target_id, item_key, channel, status, message, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("posts-target", "legacy-item", "ntfy", "sent", "legacy sent", now),
    )
    connection.execute(
        """
        INSERT INTO match_history (
            target_id, group_id, group_name, item_kind, parent_post_id,
            comment_id, item_key, author, text, permalink, include_rule,
            timestamp_text, notified_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "posts-target",
            "111",
            "legacy group",
            "post",
            "",
            "",
            "legacy-item",
            "author",
            "legacy text",
            "https://www.facebook.com/groups/111/posts/1",
            "legacy",
            "",
            now,
            now,
        ),
    )


def test_initialize_schema_rejects_existing_db_without_schema_metadata(
    tmp_path: Path,
) -> None:
    """沒有 schema_metadata 的既有 DB 不得被 current-schema repair 靜默吞掉。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY)")

    try:
        with SqliteConnection(db_path) as sqlite:
            initialize_schema(sqlite.require_connection())
    except RuntimeError as exc:
        assert "Unsupported SQLite schema version 0" in str(exc)
    else:
        raise AssertionError("existing DB without schema_metadata should fail fast")


def test_target_config_seen_scan_and_notification_roundtrip(tmp_path: Path) -> None:
    """儲存 target/config/seen/scan/notification 後可查詢或取得 id。"""

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
        loaded_target = TargetRepository(connection).get(target.id)

        assert loaded_target is not None
        assert loaded_target.scope_id == target.group_id
        assert TargetRepository(connection).list_enabled() == [loaded_target]
        assert TargetRepository(connection).list_all() == [loaded_target]

        comments_target = TargetDescriptor.for_comments(
            group_id=target.group_id,
            parent_post_id="2187454285426518",
            canonical_url=(
                "https://www.facebook.com/groups/222518561920110/posts/2187454285426518"
            ),
            name="留言 target",
        )
        TargetRepository(connection).save(comments_target)
        loaded_comments = TargetRepository(connection).find_by_kind_scope(
            TargetKind.COMMENTS,
            "222518561920110:post:2187454285426518:comments",
        )

        assert loaded_comments is not None
        assert loaded_comments.target_kind == TargetKind.COMMENTS
        assert loaded_comments.parent_post_id == "2187454285426518"
        assert loaded_comments.scope_id == "222518561920110:post:2187454285426518:comments"
        assert loaded_comments.paused

        config = TargetConfig(
            group_id=target.group_id,
            include_keywords=("票", "交換"),
            enable_desktop_notification=True,
            enable_ntfy=True,
            ntfy_topic="phase0test",
            enable_discord_notification=True,
            discord_webhook="https://discord.com/api/webhooks/example",
        )
        TargetConfigRepository(connection).save_legacy_target_config_for_migration(
            target.id,
            config,
        )
        loaded_config = TargetConfigRepository(
            connection
        ).get_legacy_target_config_for_migration(target.id)

        assert loaded_config is not None
        assert loaded_config.target_id == target.id
        assert not hasattr(loaded_config, "group_id")
        assert loaded_config.include_keywords == ("票", "交換")
        assert loaded_config.enable_desktop_notification
        assert loaded_config.enable_ntfy
        assert loaded_config.ntfy_topic == "phase0test"
        assert loaded_config.enable_discord_notification
        assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"

        global_settings = GlobalNotificationSettings(
            enable_desktop_notification=True,
            enable_ntfy=True,
            ntfy_topic="global-topic",
            enable_discord_notification=True,
            discord_webhook="https://discord.com/api/webhooks/global",
        )
        settings_repo = GlobalNotificationSettingsRepository(connection)
        settings_repo.save(global_settings)
        loaded_settings = settings_repo.get()

        assert loaded_settings.enable_desktop_notification
        assert loaded_settings.enable_ntfy
        assert loaded_settings.ntfy_topic == "global-topic"
        assert loaded_settings.enable_discord_notification
        assert loaded_settings.discord_webhook == "https://discord.com/api/webhooks/global"

        runtime_state = TargetRuntimeState(
            target_id=target.id,
            desired_state=TargetDesiredState.ACTIVE,
            runtime_status=TargetRuntimeStatus.IDLE,
        )
        TargetRuntimeStateRepository(connection).save(runtime_state)
        loaded_runtime_state = TargetRuntimeStateRepository(connection).get(target.id)

        assert loaded_runtime_state is not None
        assert loaded_runtime_state.target_id == target.id
        assert loaded_runtime_state.desired_state == TargetDesiredState.ACTIVE
        assert loaded_runtime_state.runtime_status == TargetRuntimeStatus.IDLE

        seen_repo = SeenItemRepository(connection)
        seen_item = SeenItem(
            scope_id=target.scope_id,
            item_key="item-hash",
            item_kind=ItemKind.POST,
        )
        assert seen_repo.mark_seen(seen_item)
        assert not seen_repo.mark_seen(seen_item)
        assert seen_repo.has_seen(target.scope_id, "item-hash")

        alias_item = SeenItem(
            scope_id=target.scope_id,
            item_key="primary-alias",
            item_kind=ItemKind.POST,
        )
        assert seen_repo.mark_seen_aliases(alias_item, ("primary-alias", "secondary-alias"))
        assert not seen_repo.mark_seen_aliases(alias_item, ("new-primary", "secondary-alias"))
        assert seen_repo.has_seen(target.scope_id, "new-primary")
        assert seen_repo.has_seen_any(target.scope_id, ("missing", "secondary-alias"))

        scan_id = ScanRunRepository(connection).add(
            ScanRun(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                started_at=utc_now(),
                finished_at=utc_now(),
                item_count=9,
                matched_count=1,
                metadata={"scroll_rounds": 5},
            )
        )
        assert scan_id > 0
        latest_scan = ScanRunRepository(connection).latest_by_target(target.id)
        assert latest_scan is not None
        assert latest_scan.item_count == 9
        assert latest_scan.metadata == {"scroll_rounds": 5}

        history_repo = MatchHistoryRepository(connection)
        history_id = history_repo.add(
            MatchHistoryEntry(
                target_id=target.id,
                group_id=target.group_id,
                group_name=target.group_name,
                item_kind=ItemKind.POST,
                item_key="item-hash",
                text="測試文字",
                permalink="https://www.facebook.com/groups/example/posts/1",
                include_rule="票",
            )
        )
        assert history_id > 0
        history = history_repo.list_by_target(target.id)
        assert len(history) == 1
        assert history[0].include_rule == "票"

        latest_repo = LatestScanItemRepository(connection)
        latest_repo.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_id,
                    item_kind=ItemKind.POST,
                    item_key="item-hash",
                    item_index=0,
                    author="王小明",
                    text="測試文字",
                    permalink="https://www.facebook.com/groups/example/posts/1",
                    matched_keyword="票",
                    debug_metadata={"textSource": "primary", "expandCount": 1},
                )
            ],
        )
        latest_items = latest_repo.list_by_target(target.id)
        assert len(latest_items) == 1
        assert latest_items[0].author == "王小明"
        assert latest_items[0].matched_keyword == "票"
        assert latest_items[0].debug_metadata == {"textSource": "primary", "expandCount": 1}

        event_id = NotificationEventRepository(connection).add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-hash",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )
        assert event_id > 0
        events = NotificationEventRepository(connection).list_by_target(target.id)
        assert len(events) == 1
        assert events[0].status == NotificationStatus.SENT
        assert events[0].channel == NotificationChannel.NTFY

        outbox_repo = NotificationOutboxRepository(connection)
        outbox_entry = outbox_repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:ntfy",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
                endpoint="phase0test",
                permalink="https://www.facebook.com/groups/example/posts/1",
            )
        )
        assert outbox_entry.id is not None
        assert outbox_entry.status == NotificationOutboxStatus.PENDING
        outbox_repo.mark_result(
            entry_id=outbox_entry.id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
            notification_event_id=event_id,
        )
        loaded_outbox = outbox_repo.get_by_idempotency_key(f"{target.id}:item-hash:ntfy")
        assert loaded_outbox is not None
        assert loaded_outbox.status == NotificationOutboxStatus.SENT
        assert loaded_outbox.notification_event_id == event_id


def test_notification_outbox_claim_pending_is_single_owner_across_connections(
    tmp_path: Path,
) -> None:
    """兩個 SQLite connection 不得 claim 到同一筆 pending outbox。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        NotificationOutboxRepository(connection).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:ntfy",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )

    with SqliteConnection(db_path) as sqlite_a, SqliteConnection(db_path) as sqlite_b:
        connection_a = sqlite_a.require_connection()
        connection_b = sqlite_b.require_connection()
        initialize_schema(connection_a)
        initialize_schema(connection_b)

        claimed_a = NotificationOutboxRepository(connection_a).claim_pending()
        claimed_b = NotificationOutboxRepository(connection_b).claim_pending()

        assert len(claimed_a) == 1
        assert claimed_a[0].status == NotificationOutboxStatus.PROCESSING_PENDING
        assert claimed_b == []

        NotificationOutboxRepository(connection_a).mark_result(
            entry_id=claimed_a[0].id or 0,
            status=NotificationOutboxStatus.SENT,
            attempts=claimed_a[0].attempts + 1,
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = NotificationOutboxRepository(connection)
        loaded = repo.get_by_idempotency_key(f"{target.id}:item-hash:ntfy")

    assert loaded is not None
    assert loaded.status == NotificationOutboxStatus.SENT
    assert loaded.attempts == 1


def test_notification_outbox_recovers_stale_processing_for_future_claim(
    tmp_path: Path,
) -> None:
    """過期 pending processing outbox 可回收成 pending，避免 crash 後永久卡住。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        repo = NotificationOutboxRepository(connection)
        repo.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:stale:ntfy",
                target_id=target.id,
                item_key="stale",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
        claimed = repo.claim_pending()
        assert len(claimed) == 1
        connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (claimed[0].id,),
        )

    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = NotificationOutboxRepository(connection)

        recovered_count = repo.recover_stale_processing(older_than_seconds=60)
        claimed_again = repo.claim_pending()

    assert recovered_count == 1
    assert len(claimed_again) == 1
    assert claimed_again[0].status == NotificationOutboxStatus.PROCESSING_PENDING


def test_group_config_migrates_from_legacy_target_config(tmp_path: Path) -> None:
    """正式 group config 缺資料時，會從舊 target config fallback 並保存。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)

        target = TargetDescriptor.for_group_posts(
            group_id="222518561920110",
            canonical_url="https://www.facebook.com/groups/222518561920110",
        )
        TargetRepository(connection).save(target)
        repo = TargetConfigRepository(connection)
        repo.save_legacy_target_config_for_migration(
            target.id,
            TargetConfig(
                group_id=target.group_id,
                include_keywords=("legacy",),
                fixed_refresh_sec=90,
            )
        )

        migrated = repo.get_for_target(target)
        loaded_group_config = repo.get_for_group(target.group_id)

    assert migrated is not None
    assert migrated.group_id == target.group_id
    assert migrated.include_keywords == ("legacy",)
    assert migrated.fixed_refresh_sec == 90
    assert loaded_group_config == migrated


def test_target_config_repository_legacy_methods_are_migration_only(tmp_path: Path) -> None:
    """repository 不再提供正式 target-scoped save/get 入口。"""

    db_path = tmp_path / "app.db"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        repo = TargetConfigRepository(connection)

        assert not hasattr(repo, "save")
        assert not hasattr(repo, "get")
        assert hasattr(repo, "save_legacy_target_config_for_migration")
        assert hasattr(repo, "get_legacy_target_config_for_migration")


def test_runtime_data_maintenance_clears_debug_tables_but_keeps_settings(
    tmp_path: Path,
) -> None:
    """runtime data 清理會刪除可重建資料，但保留 target/config/global settings。"""

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
        TargetConfigRepository(connection).save_legacy_target_config_for_migration(
            target.id,
            TargetConfig(group_id=target.group_id),
        )
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=target.id,
                desired_state=TargetDesiredState.ACTIVE,
                runtime_status=TargetRuntimeStatus.IDLE,
            )
        )
        GlobalNotificationSettingsRepository(connection).save(
            GlobalNotificationSettings(enable_ntfy=True, ntfy_topic="phase0test")
        )
        SeenItemRepository(connection).mark_seen(
            SeenItem(scope_id=target.scope_id, item_key="seen-key", item_kind=ItemKind.POST)
        )
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
        NotificationOutboxRepository(connection).enqueue(
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

        result = RuntimeDataMaintenanceRepository(connection).clear_runtime_data()

        assert result.scan_runs == 1
        assert result.latest_scan_items == 1
        assert result.match_history == 0
        assert result.notification_events == 1
        assert result.seen_items == 1
        assert result.notification_outbox == 1
        assert result.total_deleted == 5
        assert table_count(connection, "scan_runs") == 0
        assert table_count(connection, "latest_scan_items") == 0
        assert table_count(connection, "match_history") == 1
        assert table_count(connection, "notification_events") == 0
        assert table_count(connection, "notification_outbox") == 0
        assert table_count(connection, "seen_items") == 0
        assert TargetRepository(connection).get(target.id) is not None
        assert (
            TargetConfigRepository(connection).get_legacy_target_config_for_migration(target.id)
            is not None
        )
        assert TargetRuntimeStateRepository(connection).get(target.id) is not None
        assert GlobalNotificationSettingsRepository(connection).get().ntfy_topic == "phase0test"


def test_match_history_repository_counts_offsets_and_clears_by_target(
    tmp_path: Path,
) -> None:
    """match history repository 支援 Phase 1 target-scoped 查詢與清空。"""

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
            entry.item_key
            for entry in history.list_by_target(first_target.id, limit=2, offset=1)
        ] == [
            "first-1",
            "first-0",
        ]

        assert history.clear_by_target(first_target.id) == 3
        assert history.count_by_target(first_target.id) == 0
        assert history.count_by_target(second_target.id) == 1


def test_match_history_repository_refreshes_duplicates_and_keeps_global_limit(
    tmp_path: Path,
) -> None:
    """查看紀錄對齊 JS：重複 key 刷新到最新，且全域最多保留 10 筆。"""

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
