"""Notification secret DB-at-rest 加密測試。"""

from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.secret_storage import ENCRYPTED_SECRET_PREFIX
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import SECRET_REENCRYPTION_MARKER_KEY
from facebook_monitor.persistence.secret_storage import load_or_create_secret_codec
from facebook_monitor.persistence.secret_storage import reencrypt_plaintext_secrets_if_needed
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
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        target_row = connection.execute("SELECT * FROM target_configs").fetchone()
        outbox_row = connection.execute("SELECT * FROM notification_outbox").fetchone()

    assert target_row["ntfy_topic"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert target_row["ntfy_topic"] != "phase0test"
    assert target_row["discord_webhook"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert target_row["discord_webhook"] != "https://discord.com/api/webhooks/example"
    assert outbox_row["endpoint"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert outbox_row["endpoint"] != "https://discord.com/api/webhooks/outbox"

    with SqliteApplicationContext(db_path) as app:
        loaded_config = app.repositories.configs.get_for_target_id(target.id)
        loaded_outbox = app.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:item-hash:discord"
        )

    assert loaded_config is not None
    assert loaded_config.ntfy_topic == "phase0test"
    assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/example"
    assert loaded_outbox is not None
    assert loaded_outbox.endpoint == "https://discord.com/api/webhooks/outbox"


def test_application_context_reads_legacy_plaintext_notification_secrets(
    tmp_path: Path,
) -> None:
    """舊版 plaintext row 會被讀回並在 context 成功結束後改寫為密文。"""

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
        loaded_outbox = app.repositories.notification_outbox.get_by_idempotency_key(
            f"{target.id}:item-hash:legacy"
        )

    assert loaded_config is not None
    assert loaded_config.ntfy_topic == "legacy-topic"
    assert loaded_config.discord_webhook == "https://discord.com/api/webhooks/legacy"
    assert loaded_outbox is not None
    assert loaded_outbox.endpoint == "https://discord.com/api/webhooks/legacy-outbox"

    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        target_row = connection.execute("SELECT * FROM target_configs").fetchone()
        outbox_row = connection.execute("SELECT * FROM notification_outbox").fetchone()
        marker_row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (SECRET_REENCRYPTION_MARKER_KEY,),
        ).fetchone()

    assert target_row["ntfy_topic"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert target_row["discord_webhook"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert outbox_row["endpoint"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert marker_row is not None
    assert marker_row["value"] == "1"


def test_secret_reencryption_marker_skips_repeat_sweeps(tmp_path: Path) -> None:
    """legacy secret repair 完成後不應每次開 context 重掃 outbox。"""

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
        NotificationOutboxRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-hash:marker",
                target_id=target.id,
                item_key="item-hash",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DISCORD,
                title="title",
                message="message",
                endpoint="legacy-outbox-endpoint",
            )
        )
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, '1', '')
            """,
            (SECRET_REENCRYPTION_MARKER_KEY,),
        )
        connection.commit()
        codec = load_or_create_secret_codec(db_path, connection=connection)

        updated_count = reencrypt_plaintext_secrets_if_needed(connection, codec)
        outbox_row = connection.execute("SELECT endpoint FROM notification_outbox").fetchone()

    assert updated_count == 0
    assert outbox_row["endpoint"] == "legacy-outbox-endpoint"


def test_application_context_preserves_plaintext_that_looks_like_secret_prefix(
    tmp_path: Path,
) -> None:
    """使用者明文若以 enc:v1: 開頭，仍應當作明文加密保存並可讀回。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="測試 target",
    )
    prefixed_plaintext = "enc:v1:literal-topic"

    with SqliteApplicationContext(db_path) as app:
        app.repositories.targets.save(target)
        app.repositories.configs.save_for_target_id(
            target.id,
            TargetConfig(
                target_id=target.id,
                enable_ntfy=True,
                ntfy_topic=prefixed_plaintext,
            ),
        )

    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT ntfy_topic FROM target_configs").fetchone()

    assert row["ntfy_topic"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert row["ntfy_topic"] != prefixed_plaintext

    with SqliteApplicationContext(db_path) as app:
        loaded_config = app.repositories.configs.get_for_target_id(target.id)

    assert loaded_config is not None
    assert loaded_config.ntfy_topic == prefixed_plaintext


def test_legacy_plaintext_with_secret_prefix_is_reencrypted(tmp_path: Path) -> None:
    """legacy plaintext 剛好有 enc:v1: prefix 時，context 開啟會改寫成可解密密文。"""

    db_path = tmp_path / "app.db"
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="測試 target",
    )
    prefixed_plaintext = "enc:v1:legacy-topic"
    with SqliteConnection(db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        TargetConfigRepository(connection, secret_codec=PLAINTEXT_SECRET_CODEC).save_for_target_id(
            target.id,
            TargetConfig(
                target_id=target.id,
                enable_ntfy=True,
                ntfy_topic=prefixed_plaintext,
            ),
        )
        connection.commit()

    with SqliteApplicationContext(db_path) as app:
        loaded_config = app.repositories.configs.get_for_target_id(target.id)

    assert loaded_config is not None
    assert loaded_config.ntfy_topic == prefixed_plaintext
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT ntfy_topic FROM target_configs").fetchone()
    assert row["ntfy_topic"].startswith(ENCRYPTED_SECRET_PREFIX)
    assert row["ntfy_topic"] != prefixed_plaintext


def test_application_context_fails_fast_when_secret_key_missing_for_encrypted_db(
    tmp_path: Path,
) -> None:
    """DB 已有密文時若 secrets.key 遺失，不可靜默建立新 key。"""

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
            ),
        )

    key_path = secret_key_path_for_db(db_path)
    key_path.unlink()

    try:
        with SqliteApplicationContext(db_path):
            pass
    except ValueError as exc:
        assert "secrets.key" in str(exc)
        assert "missing" in str(exc)
    else:
        raise AssertionError("encrypted DB without secrets.key should fail fast")
