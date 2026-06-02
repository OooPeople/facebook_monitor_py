"""Support bundle privacy and readonly tests。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import zipfile

from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.diagnostics.support_bundle import prune_old_support_bundles
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


def test_support_bundle_create_runs_retention_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """建立支援包後應觸發 retention cleanup，但不影響本次 bundle。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    calls: list[dict[str, object]] = []

    def fake_prune_old_support_bundles(bundle_dir: Path, **kwargs) -> int:
        calls.append({"bundle_dir": bundle_dir, **kwargs})
        return 0

    monkeypatch.setattr(
        "facebook_monitor.diagnostics.support_bundle.prune_old_support_bundles",
        fake_prune_old_support_bundles,
    )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    assert result.path.is_file()
    assert calls
    assert calls[0]["bundle_dir"] == paths.exports_dir / "support-bundles"
    assert calls[0]["preserve"] == (result.path,)


def test_prune_old_support_bundles_limits_age_and_file_count(tmp_path: Path) -> None:
    """support bundle retention 只清 allowlisted 舊 bundle 檔案。"""

    bundle_dir = tmp_path / "support-bundles"
    bundle_dir.mkdir()
    now = datetime(2026, 6, 3, tzinfo=timezone.utc)
    created_paths: list[Path] = []
    for index in range(12):
        path = bundle_dir / f"facebook-monitor-support-20260603T0000{index:02d}Z.zip"
        path.write_bytes(b"zip")
        mtime = (now - timedelta(minutes=index)).timestamp()
        os.utime(path, (mtime, mtime))
        created_paths.append(path)
    old_path = bundle_dir / "facebook-monitor-support-20260501T000000Z.zip"
    old_path.write_bytes(b"old zip")
    old_mtime = (now - timedelta(days=40)).timestamp()
    os.utime(old_path, (old_mtime, old_mtime))
    unrelated_zip = bundle_dir / "other.zip"
    unrelated_zip.write_bytes(b"keep")
    matched_directory = bundle_dir / "facebook-monitor-support-directory.zip"
    matched_directory.mkdir()

    deleted_count = prune_old_support_bundles(
        bundle_dir,
        max_age_days=14,
        max_files=10,
        now=now,
    )

    remaining_bundle_names = {
        path.name
        for path in bundle_dir.glob("facebook-monitor-support-*.zip")
        if path.is_file()
    }
    expected_remaining = {path.name for path in created_paths[:10]}
    assert deleted_count == 3
    assert remaining_bundle_names == expected_remaining
    assert unrelated_zip.is_file()
    assert matched_directory.is_dir()


def test_prune_old_support_bundles_ignores_delete_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """清理舊支援包失敗時不應影響呼叫端建立本次 bundle。"""

    bundle_dir = tmp_path / "support-bundles"
    bundle_dir.mkdir()
    now = datetime(2026, 6, 3, tzinfo=timezone.utc)
    old_path = bundle_dir / "facebook-monitor-support-20260501T000000Z.zip"
    old_path.write_bytes(b"old zip")
    old_mtime = (now - timedelta(days=40)).timestamp()
    os.utime(old_path, (old_mtime, old_mtime))
    real_unlink = Path.unlink

    def fake_unlink(path: Path) -> None:
        if path == old_path:
            raise OSError("locked")
        real_unlink(path)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    deleted_count = prune_old_support_bundles(
        bundle_dir,
        max_age_days=14,
        max_files=10,
        now=now,
    )

    assert deleted_count == 0
    assert old_path.is_file()
