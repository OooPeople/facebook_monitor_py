"""DB invariant admin checker 測試。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.admin.check_database_invariants import classify_sqlite_error
from scripts.admin.check_database_invariants import main


def test_classify_sqlite_error_distinguishes_actionable_categories() -> None:
    """SQLite 例外需保留可行動分類，避免 admin CLI 只輸出 unknown failure。"""

    assert (
        classify_sqlite_error(sqlite3.OperationalError("database is locked")).category
        == "db_locked"
    )
    assert (
        classify_sqlite_error(sqlite3.OperationalError("database schema is locked")).category
        == "db_locked"
    )
    assert (
        classify_sqlite_error(
            sqlite3.OperationalError("unable to open database file")
        ).category
        == "db_unavailable"
    )
    assert (
        classify_sqlite_error(sqlite3.OperationalError("no such table: targets")).category
        == "schema_mismatch"
    )
    assert (
        classify_sqlite_error(sqlite3.OperationalError("near SELECT: syntax error")).category
        == "sqlite_error"
    )


def test_main_reports_missing_database_as_unavailable(
    tmp_path: Path,
    capsys,
) -> None:
    """唯讀打開不存在 DB 時應回報 db_unavailable，而不是未分類 traceback。"""

    db_path = tmp_path / "missing" / "app.db"

    assert main(["--db-path", str(db_path)]) == 2

    output = capsys.readouterr().out
    assert "ERROR: database invariant check failed" in output
    assert "reason: db_unavailable" in output
    assert str(db_path) in output


def test_main_reports_schema_mismatch_for_non_product_database(
    tmp_path: Path,
    capsys,
) -> None:
    """不是本產品 schema 的 SQLite DB 應回報 schema_mismatch。"""

    db_path = tmp_path / "app.db"
    sqlite3.connect(db_path).close()

    assert main(["--db-path", str(db_path)]) == 2

    output = capsys.readouterr().out
    assert "reason: schema_mismatch" in output
    assert "no such table" in output
