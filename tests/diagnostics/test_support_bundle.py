"""Support bundle privacy and readonly tests。"""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.targets import TargetRepository
from facebook_monitor.persistence.schema import initialize_schema
from facebook_monitor.persistence.secret_storage import PlaintextSecretCodec
from facebook_monitor.persistence.secret_storage import SECRET_REENCRYPTION_MARKER_KEY
from facebook_monitor.persistence.sqlite_connection import SqliteConnection
from facebook_monitor.runtime.paths import resolve_runtime_paths


def test_support_bundle_database_summary_is_readonly(tmp_path: Path) -> None:
    """匯出支援包不得順手 re-encrypt legacy plaintext secrets。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
    )
    with SqliteConnection(paths.db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        TargetRepository(connection).save(target)
        TargetConfigRepository(
            connection,
            secret_codec=PlaintextSecretCodec(),
        ).save_for_target_id(
            target.id,
            TargetConfig(
                target_id=target.id,
                enable_ntfy=True,
                ntfy_topic="legacy-plaintext-topic",
            ),
        )

    create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    with SqliteConnection(paths.db_path) as sqlite:
        connection = sqlite.require_connection()
        topic_row = connection.execute(
            "SELECT ntfy_topic FROM target_configs WHERE target_id = ?",
            (target.id,),
        ).fetchone()
        marker_row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (SECRET_REENCRYPTION_MARKER_KEY,),
        ).fetchone()
    assert topic_row["ntfy_topic"] == "legacy-plaintext-topic"
    assert marker_row is None


def test_support_bundle_hashes_invariant_row_ids(tmp_path: Path) -> None:
    """支援包內 invariant row id 不可直接包含 group/item identifiers。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    now = "2026-05-22T00:00:00+00:00"
    with SqliteConnection(paths.db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO seen_items (
                scope_id, item_key, item_kind, parent_post_id, comment_id,
                first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "222518561920110",
                "9999999999999999:comment:4444444444444444",
                "unexpected_kind",
                "",
                "",
                now,
                now,
            ),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    with zipfile.ZipFile(result.path) as archive:
        payload = json.loads(archive.read("database_summary.json").decode("utf-8"))
        summary_text = json.dumps(payload, ensure_ascii=False)
    assert payload["invariant_violation_count"] == 1
    assert payload["invariant_violations"][0]["row_id_hash"]
    assert "row_id" not in payload["invariant_violations"][0]
    assert "222518561920110" not in summary_text
    assert "9999999999999999" not in summary_text
    assert "4444444444444444" not in summary_text


def test_support_bundle_readme_marks_redaction_as_best_effort(tmp_path: Path) -> None:
    """支援包 README 必須提醒 redaction 是 best-effort，分享前仍需檢查。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    with zipfile.ZipFile(result.path) as archive:
        readme = archive.read("README.txt").decode("utf-8")
    assert "best-effort" in readme
    assert "review the extracted files before sharing" in readme


def test_support_bundle_redacts_runtime_diagnostics_secrets(tmp_path: Path) -> None:
    """runtime diagnostics 內的 webhook、token 與使用者路徑不可原樣進支援包。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    diagnostics = (
        "notification failed https://discord.com/api/webhooks/123456/private-token "
        "callback=https://example.test/hook?token=secret "
        r"C:\Users\alice\facebook_monitor_data\logs\error.log"
    )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text=diagnostics,
        app_metadata={},
    )

    with zipfile.ZipFile(result.path) as archive:
        runtime_text = archive.read("runtime_diagnostics.txt").decode("utf-8")
    assert "private-token" not in runtime_text
    assert "token=secret" not in runtime_text
    assert r"C:\Users\alice" not in runtime_text
