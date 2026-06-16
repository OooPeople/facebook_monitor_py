"""Frontend vendor provenance contract tests。"""

from __future__ import annotations

import json
from pathlib import Path


def test_sortablejs_vendor_provenance_matches_bundled_files() -> None:
    """vendored SortableJS 檔案需由 manifest 與文件共同追蹤來源。"""

    doc = Path("docs/frontend-vendor.md").read_text(encoding="utf-8")
    manifest_path = Path(
        "src/facebook_monitor/webapp/static/vendor/frontend-vendor.manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = {asset["path"]: asset for asset in manifest["assets"]}
    module_path = "src/facebook_monitor/webapp/static/vendor/sortablejs/sortable.esm.js"
    license_path = "src/facebook_monitor/webapp/static/vendor/sortablejs/LICENSE"

    assert "SortableJS" in doc
    assert manifest_path.as_posix() in doc
    assert module_path in doc
    assert license_path in doc
    assert assets[module_path]["version"] == "1.15.6"
    assert assets[module_path]["license"] == "MIT"
    assert assets[license_path]["version"] == "1.15.6"
    assert assets[license_path]["license"] == "MIT"
