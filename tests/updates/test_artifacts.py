"""Release artifact naming helper tests。"""

from __future__ import annotations

from facebook_monitor.updates.artifacts import sanitize_release_asset_name


def test_sanitize_release_asset_name_rejects_paths() -> None:
    """Release asset name 不能偷渡路徑。"""

    assert sanitize_release_asset_name("facebook-monitor-0.1.0-windows-portable.zip")
    for value in ("../app.zip", "folder/app.zip", "app zip", ".", ".."):
        try:
            sanitize_release_asset_name(value)
        except ValueError as exc:
            assert str(exc) == "invalid_asset_name"
        else:
            raise AssertionError("expected invalid asset name")
