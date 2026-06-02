"""Support bundle privacy and readonly tests。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import zipfile

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import ScanRun
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetRuntimeState
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.diagnostics.support_bundle import create_support_bundle
from facebook_monitor.diagnostics.support_bundle import prune_old_support_bundles
from facebook_monitor.persistence.repositories.latest_scan_items import LatestScanItemRepository
from facebook_monitor.persistence.repositories.notification_outbox import NotificationOutboxRepository
from facebook_monitor.persistence.repositories.scan_runs import ScanRunRepository
from facebook_monitor.persistence.repositories.target_configs import TargetConfigRepository
from facebook_monitor.persistence.repositories.target_runtime_state import (
    TargetRuntimeStateRepository,
)
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
        r"C:\Users\alice\facebook_monitor_data\logs\error.log "
        r"E:\PrivateProject\CustomerName\app.db "
        "作者王小明 家庭住址 targetId=short-target\n"
        "CustomerName: 家庭住址"
    )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text=diagnostics,
        app_metadata={
            "app_version": "1.2.3",
            "private_meta": "E:\\PrivateProject\\CustomerName 作者王小明",
        },
    )

    with zipfile.ZipFile(result.path) as archive:
        runtime_text = archive.read("runtime_diagnostics.txt").decode("utf-8")
        metadata_text = archive.read("metadata.json").decode("utf-8")
    assert "private-token" not in runtime_text
    assert "token=secret" not in runtime_text
    assert r"C:\Users\alice" not in runtime_text
    assert "CustomerName" not in runtime_text
    assert "作者王小明" not in runtime_text
    assert "家庭住址" not in runtime_text
    assert "short-target" not in runtime_text
    assert "1.2.3" in metadata_text
    assert "private_meta" not in metadata_text
    assert "CustomerName" not in metadata_text
    assert "作者王小明" not in metadata_text


def test_support_bundle_preserves_known_scan_stop_reason_counts(
    tmp_path: Path,
) -> None:
    """現有 worker stop reason 應保留聚合；未知 code 不輸出原值。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    known_reasons = (
        "sort_adjust_unconfirmed_skip",
        "seen_stop_consecutive_seen",
        "auto_load_more_disabled",
        "no_comment_round_stats",
        "comment_scroll_stalled",
        "comment_stagnant_windows",
        "comment_scroll_rounds_completed",
        "comment_collection_stopped",
        "comment_load_more_guard_active",
    )
    now = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)
    with SqliteConnection(paths.db_path) as sqlite:
        connection = sqlite.require_connection()
        initialize_schema(connection)
        for index, reason in enumerate((*known_reasons, "privateKeyValue"), start=1):
            target = TargetDescriptor.for_group_posts(
                group_id=f"900000000000{index:02d}",
                canonical_url=f"https://www.facebook.com/groups/900000000000{index:02d}",
            )
            TargetRepository(connection).save(target)
            ScanRunRepository(connection).add(
                ScanRun(
                    target_id=target.id,
                    status=ScanStatus.SUCCESS,
                    started_at=now,
                    finished_at=now,
                    metadata={"stop_reason": reason},
                )
            )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    with zipfile.ZipFile(result.path) as archive:
        scan_payload = json.loads(archive.read("scan_summaries.json").decode("utf-8"))

    assert scan_payload["stop_reason_counts"] == {
        **{reason: 1 for reason in known_reasons},
        "unrecognized_code": 1,
    }
    assert "privateKeyValue" not in json.dumps(scan_payload, ensure_ascii=False)


def test_support_bundle_includes_redacted_debug_sections(tmp_path: Path) -> None:
    """標準支援包應包含 runtime/scan/log 摘要，但不外洩原始內容。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    target = TargetDescriptor.for_group_posts(
        group_id="222518561920110",
        canonical_url="https://www.facebook.com/groups/222518561920110",
        name="私密社團名稱",
    )
    now = datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)
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
                include_keywords=("秘密關鍵字",),
                enable_discord_notification=True,
                discord_webhook="https://discord.com/api/webhooks/123456/private-token",
            ),
        )
        TargetRuntimeStateRepository(connection).save(
            TargetRuntimeState(
                target_id=target.id,
                runtime_status=TargetRuntimeStatus.RUNNING,
                active_worker_id="resident-slot-1",
                active_page_id="page-raw-id",
                last_heartbeat_at=now,
                last_skip_reason="visited https://www.facebook.com/groups/222518561920110",
            )
        )
        scan_run_id = ScanRunRepository(connection).add(
            ScanRun(
                target_id=target.id,
                status=ScanStatus.FAILED,
                started_at=now,
                finished_at=now,
                item_count=1,
                matched_count=1,
                error_message=(
                    "extractor_failed: "
                    "https://www.facebook.com/groups/222518561920110/posts/999"
                ),
                worker_mode=WorkerMode.HEADLESS,
                metadata={
                    "worker": "resident",
                    "scope_id": "222518561920110",
                    "targetId": target.id,
                    "stop_reason": "extractor_failed",
                    "candidate_count": 1,
                    "message": "這是 metadata 內的完整貼文/留言",
                    "source": "privateKeyValue",
                    "作者王小明": "privateKeyValue",
                    "nested": {
                        "source": "privateKeyValue",
                        "作者王小明": "家庭住址",
                    },
                    "rounds": [{"round_index": 1, "raw_item_count": 1}],
                },
            )
        )
        LatestScanItemRepository(connection).replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=scan_run_id,
                    item_kind=ItemKind.POST,
                    item_key="9999999999999999",
                    item_index=0,
                    author="秘密作者",
                    text="這是不能外流的完整貼文內容",
                    permalink="https://www.facebook.com/groups/222518561920110/posts/9999999999999999",
                    matched_keyword="秘密關鍵字",
                    debug_metadata={
                        "textSource": "primary",
                        "postId": "9999999999999999",
                        "permalink": "https://www.facebook.com/private",
                        "source": "privateKeyValue",
                        "customSafeLooking": "家庭住址",
                        "作者王小明": "privateKeyValue",
                    },
                    scanned_at=now,
                )
            ],
        )
        NotificationOutboxRepository(
            connection,
            secret_codec=PlaintextSecretCodec(),
        ).enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:failed:discord",
                target_id=target.id,
                item_key="9999999999999999",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DISCORD,
                title="秘密標題",
                message="秘密通知內容",
                endpoint="https://discord.com/api/webhooks/123456/private-token",
                permalink="https://www.facebook.com/private",
                status=NotificationOutboxStatus.FAILED,
                attempts=2,
                last_error="failed https://discord.com/api/webhooks/123456/private-token",
            )
        )
    (paths.logs_dir / "app.log").write_text(
        f"target_id={target.id} item_key=9999999999999999 "
        "targetId=short-target itemKey=abc "
        "worker_id=resident-slot-1 page_id=page-raw-id "
        "failed https://discord.com/api/webhooks/123456/private-token "
        "url=https://www.facebook.com/groups/222518561920110/posts/999 "
        r"E:\PrivateProject\CustomerName\app.db 作者王小明 家庭住址",
        encoding="utf-8",
    )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
        scheduler_state={
            "running": True,
            "current_running_count": 1,
            "current_queued_count": 1,
            "max_concurrent_scans": 4,
            "queued_target_ids": (target.id,),
            "worker_ids": ("resident-slot-1",),
        },
    )

    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        json_payloads = {
            name: json.loads(archive.read(name).decode("utf-8"))
            for name in names
            if name.endswith(".json")
        }
        combined_text = "\n".join(
            archive.read(name).decode("utf-8")
            for name in sorted(names)
        )
        scheduler_payload = json.loads(archive.read("scheduler_state.json").decode("utf-8"))
        scan_payload = json.loads(archive.read("scan_summaries.json").decode("utf-8"))
        dedupe_payload = json.loads(archive.read("dedupe_summary.json").decode("utf-8"))
        log_payload = json.loads(archive.read("log_tail.json").decode("utf-8"))

    assert names == {
        "README.txt",
        "metadata.json",
        "runtime_diagnostics.txt",
        "runtime_paths.json",
        "database_summary.json",
        "bundle_manifest.json",
        "database_health.json",
        "target_inventory.json",
        "target_runtime_states.json",
        "scan_summaries.json",
        "latest_scan_debug_summary.json",
        "notification_diagnostics.json",
        "dedupe_summary.json",
        "profile_session.json",
        "maintenance_update_summary.json",
        "scheduler_state.json",
        "log_tail.json",
    }
    assert all(payload is not None for payload in json_payloads.values())
    assert scheduler_payload["queued_targets"] == ["target_001"]
    assert scan_payload["stop_reason_counts"] == {"extractor_failed": 1}
    assert dedupe_payload["available"] is True
    assert {
        "scan_scope_state",
        "target_dedupe_state",
        "logical_item_counts",
        "seen_item_counts",
        "notification_dedupe_counts",
    }.issubset(dedupe_payload)
    assert log_payload["files"][0]["exists"] is True
    assert "target_001" in combined_text
    assert "worker_001" in combined_text
    assert target.id not in combined_text
    assert "222518561920110" not in combined_text
    assert "9999999999999999" not in combined_text
    assert "page-raw-id" not in combined_text
    assert "short-target" not in combined_text
    assert "abc" not in combined_text
    assert "私密社團名稱" not in combined_text
    assert "秘密關鍵字" not in combined_text
    assert "秘密作者" not in combined_text
    assert "這是不能外流的完整貼文內容" not in combined_text
    assert "秘密通知內容" not in combined_text
    assert "這是 metadata 內的完整貼文/留言" not in combined_text
    assert "作者王小明" not in combined_text
    assert "家庭住址" not in combined_text
    assert "privateKeyValue" not in combined_text
    assert "CustomerName" not in combined_text
    assert "private-token" not in combined_text
    assert "https://www.facebook.com" not in combined_text
    assert r"C:\Users\alice" not in combined_text


def test_support_bundle_log_tail_reads_only_bounded_tail(tmp_path: Path) -> None:
    """log tail 只保留尾端固定上限，不把大檔整段輸出。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()
    old_line = "old secret target_id=old-target item_key=1234567890123456\n"
    filler_line = "filler line without sensitive values\n"
    tail_line = "tail marker target_id=tail-target item_key=9876543210987654\n"
    (paths.logs_dir / "app.log").write_text(
        old_line + filler_line * 5000 + tail_line,
        encoding="utf-8",
    )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    with zipfile.ZipFile(result.path) as archive:
        payload = json.loads(archive.read("log_tail.json").decode("utf-8"))
        combined_text = json.dumps(payload, ensure_ascii=False)

    app_log = payload["files"][0]
    assert app_log["exists"] is True
    assert app_log["truncated_bytes"] is True
    assert app_log["truncated_lines"] is True
    assert any(line["has_identifier"] for line in app_log["lines"])
    assert "old secret" not in combined_text
    assert "tail-target" not in combined_text
    assert "9876543210987654" not in combined_text


def test_support_bundle_section_failure_is_isolated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """單一 collector 失敗時只標記該 section unavailable，不中斷整包。"""

    paths = resolve_runtime_paths(data_dir=tmp_path / "data", app_base_dir=tmp_path / "app")
    paths.ensure_writable_dirs()

    def fail_database_health(*_args, **_kwargs):
        raise RuntimeError("private path E:\\PrivateProject\\CustomerName\\app.db")

    monkeypatch.setattr(
        "facebook_monitor.diagnostics.support_bundle._database_health_payload",
        fail_database_health,
    )

    result = create_support_bundle(
        paths=paths,
        runtime_diagnostics_text="",
        app_metadata={},
    )

    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
        payload = json.loads(archive.read("database_health.json").decode("utf-8"))
        manifest = json.loads(archive.read("bundle_manifest.json").decode("utf-8"))
        readme = archive.read("README.txt").decode("utf-8")

    database_health_section = next(
        section
        for section in manifest["sections"]
        if section["name"] == "database_health"
    )
    assert payload == {"available": False, "error": "RuntimeError"}
    assert database_health_section["status"] == "unavailable"
    assert database_health_section["error"] == "RuntimeError"
    assert "README.txt" in names
    assert "private path" not in json.dumps(payload, ensure_ascii=False)
    assert "CustomerName" not in json.dumps(manifest, ensure_ascii=False)
    assert "best-effort" in readme


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
