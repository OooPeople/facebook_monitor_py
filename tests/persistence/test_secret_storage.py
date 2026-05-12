"""Notification secret DB-at-rest 加密測試。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import GlobalNotificationSettings
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.repositories.global_notification_settings import (
    GlobalNotificationSettingsRepository,
)
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.secret_storage import ENCRYPTED_SECRET_PREFIX
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import secret_key_path_for_db
from facebook_monitor.persistence.sqlite_connection import SqliteConnection


PLAINTEXT_SECRET_CODEC = PlaintextSecretCodec()


def test_application_context_encrypts_notification_secrets_at_rest(tmp_path: Path) -> None:
    """正式 application context 寫入時，SQLite raw value 不應再是明文。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="測試 target",
    )

    with SqliteApplicationContext(db_path) as app:
        app.repositories.targets.save(target)
        app.repositories.configs.save_for_target_id(
            target.id,
            TargetConfig(
                target_id=target.id,
                enable_ntfy=True,
                ntfy_topic="phase0test",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/example",
            ),
        )
        app.repositories.global_notification_settings.save(
            GlobalNotificationSettings(
                enable_ntfy=True,
                ntfy_topic="global-topic",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/global",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:discord",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DISCORD,
                title="title",
                message="message",
                endpoint="https://discord.com/api/webhooks/outbox",
            )
        )

    assert secret_key_path_for_db(db_path).exists()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        target_row = connection.execute("SELECT * FROM target_configs").fetchone()
        global_row = connection.execute("SELECT * FROM global_notification_settings").fetchone()
        outbox_row = connection.execute("SELECT * FROM notification_outbox").fetchone()

    assert target_row["ntfy_topic"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert target_row["ntfy_topic"] != "phase0test"
    assert target_row["discord_webhook"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert target_row["discord_webhook"] != "https://discord.com/api/webhooks/example"
    assert global_row["ntfy_topic"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert global_row["ntfy_topic"] != "global-topic"
    assert global_row["discord_webhook"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert global_row["discord_webhook"] != "https://discord.com/api/webhooks/global"
    assert outbox_row["endpoint"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert outbox_row["endpoint"] != "https://discord.com/api/webhooks/outbox"

    with SqliteApplicationContext(db_path) as app:
        loaded_config = app.repositories.configs.get_for_target_id(target.id)
        loaded_settings = app.repositories.global_notification_settings.get()
        loaded_outbox = app.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:item-hash:discord"
        )

    assert loaded_config is not None
    assert loaded_config.ntfy_topic == "phase0test"
    assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"
    assert loaded_settings.ntfy_topic == "global-topic"
    assert loaded_settings.discord_webhook == "https://discord.com/api/webhooks/global"
    assert loaded_outbox is not None
    assert loaded_outbox.endpoint == "https://discord.com/api/webhooks/outbox"


def test_application_context_reads_legacy_plaintext_notification_secrets(
    tmp_path: Path,
) -> None:
    """舊版 plaintext row 仍可被正式 repository 讀回，避免升級後日常操作中斷。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="測試 target",
    )
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        TargetConfigRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC).save_for_target_id(
            target.id,
            TargetConfig(
                target_id=target.id,
                enable_ntfy=True,
                ntfy_topic="legacy-topic",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/legacy",
            ),
        )
        GlobalNotificationSettingsRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC).save(
            GlobalNotificationSettings(
                enable_ntfy=True,
                ntfy_topic="legacy-global",
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/legacy-global",
            )
        )
        NotificationOutboxRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:legacy",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DISCORD,
                title="title",
                message="message",
                endpoint="https://discord.com/api/webhooks/legacy-outbox",
            )
        )
        connection.commit()

    with SqliteApplicationContext(db_path) as app:
        loaded_config = app.repositories.configs.get_for_target_id(target.id)
        loaded_settings = app.repositories.global_notification_settings.get()
        loaded_outbox = app.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:item-hash:legacy"
        )

    assert loaded_config is not None
    assert loaded_config.ntfy_topic == "legacy-topic"
    assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/legacy"
    assert loaded_settings.ntfy_topic == "legacy-global"
    assert loaded_settings.discord_webhook == "https://discord.com/api/webhooks/legacy-global"
    assert loaded_outbox is not None
    assert loaded_outbox.endpoint == "https://discord.com/api/webhooks/legacy-outbox"

