"""Standalone updater CLI 測試。"""

from __future__ import annotations

import json
from pathlib import Path

from facebook_monitor.updater import main
from facebook_monitor.updates.apply import UpdaterApplyResult
from facebook_monitor.updates.manifest import release_manifest_asset_name
from facebook_monitor.updates.manifest import release_manifest_signature_asset_name


def test_updater_restart_invalid_pending_does_not_crash(
    tmp_path: Path,
    capsys,
) -> None:
    """`--restart` 遇到壞 pending 檔時仍走一般失敗結果與 updater log。"""

    data_dir = tmp_path / "data"
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text("{", encoding="utf-8")

    exit_code = main(
        [
            "--data-dir",
            str(data_dir),
            "--pending-update",
            str(pending_path),
            "--restart",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "failed:" in captured.out
    assert "status=failed applied=false" in (data_dir / "logs" / "updater.log").read_text(
        encoding="utf-8"
    )


def test_updater_restart_uses_pending_loaded_before_success_cleanup(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """成功套用會清掉 pending；restart 必須使用清理前已讀好的 handoff。"""

    data_dir = tmp_path / "data"
    runtime_dir = data_dir / "runtime"
    updates_dir = data_dir / "updates" / "0.1.0"
    updates_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    zip_path = updates_dir / "update.zip"
    zip_path.write_bytes(b"zip")
    manifest_path = updates_dir / release_manifest_asset_name("0.1.0")
    signature_path = updates_dir / release_manifest_signature_asset_name("0.1.0")
    manifest_path.write_text("manifest", encoding="utf-8")
    signature_path.write_text("sig", encoding="utf-8")
    pending_path = runtime_dir / "pending_update.json"
    pending_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.1.0",
                "repository": "OooPeople/facebook_monitor_py",
                "asset_name": "facebook-monitor-0.1.0-windows-portable.zip",
                "zip_path": str(zip_path),
                "expected_sha256": "a" * 64,
                "actual_sha256": "a" * 64,
                "app_base_dir": str(tmp_path / "app"),
                "data_dir": str(data_dir),
                "db_path": str(data_dir / "app.db"),
                "profile_dir": str(data_dir / "profiles" / "automation_default"),
                "logs_dir": str(data_dir / "logs"),
                "runtime_dir": str(runtime_dir),
                "created_at": "2026-05-17T00:00:00+00:00",
                "manifest_path": str(manifest_path),
                "manifest_signature_path": str(signature_path),
                "manifest_sha256": "b" * 64,
                "manifest_key_id": "test-key",
            }
        ),
        encoding="utf-8",
    )
    restarted_versions: list[str] = []

    def fake_apply_loaded_pending_update_file(*args, **kwargs) -> UpdaterApplyResult:
        pending_path.unlink()
        return UpdaterApplyResult(status="applied", applied=True, message="updated")

    def fake_launch_restarted_app(pending):
        restarted_versions.append(pending.version)
        from facebook_monitor.updates.launcher import AppRestartResult

        return AppRestartResult(
            launched=True,
            status="launched",
            message="app launched",
            pid=123,
        )

    monkeypatch.setattr(
        "facebook_monitor.updater.apply_loaded_pending_update_file",
        fake_apply_loaded_pending_update_file,
    )
    monkeypatch.setattr(
        "facebook_monitor.updater.launch_restarted_app",
        fake_launch_restarted_app,
    )

    exit_code = main(
        [
            "--data-dir",
            str(data_dir),
            "--pending-update",
            str(pending_path),
            "--restart",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert restarted_versions == ["0.1.0"]
    assert "restart: launched: app launched" in captured.out
