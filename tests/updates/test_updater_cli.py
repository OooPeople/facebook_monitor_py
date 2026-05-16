"""Standalone updater CLI 測試。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.updater import main


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
