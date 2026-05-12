"""Build metadata tests。"""

from __future__ import annotations

from facebook_monitor.runtime.build_metadata import BUILD_DATE_ENV
from facebook_monitor.runtime.build_metadata import GIT_COMMIT_ENV
from facebook_monitor.runtime.build_metadata import PACKAGING_MODE_ENV
from facebook_monitor.runtime.build_metadata import collect_build_metadata


def test_collect_build_metadata_uses_source_defaults() -> None:
    """未注入 build 環境變數時，metadata 保留 source mode 預設值。"""

    metadata = collect_build_metadata(asset_version="asset-test")

    assert metadata.app_name == "Facebook Monitor"
    assert metadata.app_version == "0.0.0"
    assert metadata.asset_version == "asset-test"
    assert metadata.python_version
    assert metadata.executable.exists()
    assert metadata.packaging_mode in {"source", "frozen"}
    assert metadata.build_date == "unknown"
    assert metadata.git_commit == "unknown"


def test_collect_build_metadata_reads_packaging_env(monkeypatch) -> None:
    """打包流程可用環境變數注入 build metadata。"""

    monkeypatch.setenv(BUILD_DATE_ENV, "2026-05-10T00:00:00Z")
    monkeypatch.setenv(GIT_COMMIT_ENV, "abc1234")
    monkeypatch.setenv(PACKAGING_MODE_ENV, "portable")

    metadata = collect_build_metadata(asset_version="asset-test")

    assert metadata.packaging_mode == "portable"
    assert metadata.build_date == "2026-05-10T00:00:00Z"
    assert metadata.git_commit == "abc1234"
