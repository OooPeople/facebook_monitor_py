"""Application housekeeping tests。"""

from __future__ import annotations

from pathlib import Path

from facebook_monitor.application.maintenance import run_bounded_retention_maintenance_for_db


def test_bounded_retention_maintenance_does_not_create_missing_db(
    tmp_path: Path,
) -> None:
    """housekeeping helper 不應為了清理而建立不存在的 SQLite DB。"""

    db_path = tmp_path / "missing" / "app.db"

    deleted_count = run_bounded_retention_maintenance_for_db(db_path)

    assert deleted_count == 0
    assert not db_path.exists()
