"""唯讀檢查 SQLite DB 內的產品資料 invariant。"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.persistence.sqlite_retry import is_sqlite_lock_error
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths


@dataclass(frozen=True)
class DatabaseInvariantCheckError:
    """描述 DB invariant checker 執行階段的可分類錯誤。"""

    category: str
    detail: str


def build_parser() -> argparse.ArgumentParser:
    """建立 CLI parser。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser


def resolve_db_path(*, db_path: Path | None, data_dir: Path | None) -> Path:
    """依參數解析要檢查的 DB path。"""

    if db_path is not None:
        return db_path
    if data_dir is not None:
        return resolve_runtime_paths(data_dir=data_dir).db_path
    return default_runtime_paths().db_path


def classify_sqlite_error(exc: sqlite3.Error) -> DatabaseInvariantCheckError:
    """將 SQLite 例外轉成 admin CLI 可行動的錯誤分類。"""

    detail = str(exc).strip() or exc.__class__.__name__
    normalized = detail.casefold()
    if is_sqlite_lock_error(exc):
        return DatabaseInvariantCheckError(category="db_locked", detail=detail)
    if (
        "unable to open database file" in normalized
        or "no such file" in normalized
        or "cannot open" in normalized
    ):
        return DatabaseInvariantCheckError(category="db_unavailable", detail=detail)
    if (
        "no such table" in normalized
        or "no such column" in normalized
        or "malformed database schema" in normalized
        or "file is not a database" in normalized
    ):
        return DatabaseInvariantCheckError(category="schema_mismatch", detail=detail)
    return DatabaseInvariantCheckError(category="sqlite_error", detail=detail)


def print_check_error(*, db_path: Path, error: DatabaseInvariantCheckError) -> None:
    """輸出 DB invariant checker 的分類錯誤。"""

    print(f"ERROR: database invariant check failed in {db_path}")
    print(f"reason: {error.category}")
    print(f"detail: {error.detail}")


def main(argv: list[str] | None = None) -> int:
    """執行 DB invariant checker。"""

    args = build_parser().parse_args(argv)
    db_path = resolve_db_path(db_path=args.db_path, data_dir=args.data_dir)
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        violations = validate_database_invariants(connection)
    except sqlite3.Error as exc:
        print_check_error(db_path=db_path, error=classify_sqlite_error(exc))
        return 2
    finally:
        if connection is not None:
            connection.close()
    if not violations:
        print(f"OK: no database invariant violations found in {db_path}")
        return 0
    print(f"Found {len(violations)} database invariant violation(s) in {db_path}:")
    for violation in violations:
        print(f"- {violation.format()}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
