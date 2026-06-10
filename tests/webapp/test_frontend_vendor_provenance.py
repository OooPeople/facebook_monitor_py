"""Frontend vendor provenance contract tests。"""

from __future__ import annotations

import hashlib
from pathlib import Path


def test_sortablejs_vendor_provenance_matches_bundled_files() -> None:
    """vendored SortableJS 檔案異動時，來源文件也必須同步更新。"""

    doc = Path("docs/frontend-vendor.md").read_text(encoding="utf-8")
    module_path = Path("src/facebook_monitor/webapp/static/vendor/sortablejs/sortable.esm.js")
    license_path = Path("src/facebook_monitor/webapp/static/vendor/sortablejs/LICENSE")

    assert "SortableJS" in doc
    assert "`1.15.6`" in doc
    assert module_path.as_posix() in doc
    assert license_path.as_posix() in doc
    assert _sha256(module_path) in doc
    assert _sha256(license_path) in doc


def _sha256(path: Path) -> str:
    """回傳文件內使用的大寫 SHA256 表示法。"""

    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
