"""Notification secret 加密保存。

職責：在 persistence boundary 內處理 notification endpoint / topic 的
DB-at-rest 加解密，讓 application、worker 與 Web UI 仍只看見明文值。
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken


ENCRYPTED_SECRET_PREFIX = "enc:v1:"
DEFAULT_SECRET_KEY_FILENAME = "secrets.key"


class SecretCodec:
    """用 Fernet 加解密 SQLite 內保存的 secret 字串。"""

    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    def encrypt(self, value: str) -> str:
        """將明文 secret 轉成可保存於 SQLite 的密文。"""

        if not value:
            return ""
        if value.startswith(ENCRYPTED_SECRET_PREFIX):
            return value
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{ENCRYPTED_SECRET_PREFIX}{token}"

    def decrypt(self, value: str) -> str:
        """將 SQLite 內的 secret 還原成明文；舊版明文資料維持可讀。"""

        if not value:
            return ""
        if not value.startswith(ENCRYPTED_SECRET_PREFIX):
            return value
        token = value.removeprefix(ENCRYPTED_SECRET_PREFIX).encode("ascii")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("encrypted notification secret cannot be decrypted") from exc


class PlaintextSecretCodec:
    """測試或舊相容路徑使用的 no-op codec。"""

    def encrypt(self, value: str) -> str:
        """原樣回傳 value。"""

        return value

    def decrypt(self, value: str) -> str:
        """原樣回傳 value。"""

        return value


def secret_key_path_for_db(db_path: Path) -> Path:
    """依 DB 位置推導本機 secret key 檔案路徑。"""

    return db_path.parent / DEFAULT_SECRET_KEY_FILENAME


def load_or_create_secret_codec(db_path: Path) -> SecretCodec:
    """載入或建立 DB 專用的 local encryption key。"""

    key_path = secret_key_path_for_db(db_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key + b"\n")
        key_path.chmod(0o600)
    try:
        return SecretCodec(Fernet(key))
    except ValueError as exc:
        raise ValueError(f"invalid notification secret key file: {key_path}") from exc


def reencrypt_plaintext_secrets(
    connection: sqlite3.Connection,
    secret_codec: SecretCodec,
) -> int:
    """將 legacy plaintext notification secrets 原地改寫為 enc:v1 密文。"""

    updated_count = 0
    for table_name, column_name in (
        ("target_configs", "ntfy_topic"),
        ("target_configs", "discord_webhook"),
        ("global_notification_settings", "ntfy_topic"),
        ("global_notification_settings", "discord_webhook"),
        ("notification_outbox", "endpoint"),
        ("sidebar_group_config_templates", "ntfy_topic"),
        ("sidebar_group_config_templates", "discord_webhook"),
    ):
        if not _table_has_column(connection, table_name, column_name):
            continue
        rows = connection.execute(
            f"""
            SELECT rowid AS secret_rowid, {column_name}
            FROM {table_name}
            WHERE {column_name} <> ''
              AND {column_name} NOT LIKE ?
            """,
            (f"{ENCRYPTED_SECRET_PREFIX}%",),
        ).fetchall()
        for row in rows:
            connection.execute(
                f"UPDATE {table_name} SET {column_name} = ? WHERE rowid = ?",
                (secret_codec.encrypt(str(row[column_name])), row["secret_rowid"]),
            )
            updated_count += 1
    return updated_count


def _table_has_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    """回傳 SQLite table 是否存在指定欄位。"""

    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    if row is None:
        return False
    columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(column["name"]) == column_name for column in columns)
