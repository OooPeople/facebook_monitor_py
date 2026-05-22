"""唯讀檢查 SQLite DB 內的產品資料 invariant。"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from facebook_monitor.persistence.invariants import validate_database_invariants
from facebook_monitor.runtime.paths import default_runtime_paths
from facebook_monitor.runtime.paths import resolve_runtime_paths


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


def main(argv: list[str] | None = None) -> int:
    """執行 DB invariant checker。"""

    args = build_parser().parse_args(argv)
    db_path = resolve_db_path(db_path=args.db_path, data_dir=args.data_dir)
    uri = f"file:{db_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        violations = validate_database_invariants(connection)
    finally:
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
