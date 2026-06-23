"""Facebook group metadata validation tests。"""

from __future__ import annotations

import pytest

from facebook_monitor.facebook.group_metadata_validation import UNAVAILABLE_BODY_MARKERS
from facebook_monitor.facebook.group_metadata_validation import body_mentions_unavailable_page
from facebook_monitor.facebook.group_metadata_validation import final_url_matches_expected_group
from facebook_monitor.facebook.group_metadata_validation import has_polluted_group_cover_image_url
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name


def test_group_metadata_validation_detects_error_page_signals() -> None:
    """metadata validation 需辨識 Facebook 錯誤頁名稱、body 與通用圖。"""

    assert is_invalid_facebook_group_name("Facebook | Error")
    assert body_mentions_unavailable_page("Sorry, something went wrong.")
    assert has_polluted_group_cover_image_url(
        "https://static.facebook.com/images/logos/facebook_2x.png"
    )


@pytest.mark.parametrize("marker", UNAVAILABLE_BODY_MARKERS)
def test_body_unavailable_detection_covers_all_markers(marker: str) -> None:
    """Facebook unavailable body marker 需支援英文與繁中，並容忍大小寫與空白。"""

    noisy_body = f"\n  prefix {marker.upper()} \n suffix"

    assert body_mentions_unavailable_page(noisy_body)


def test_final_url_validation_uses_supported_facebook_hosts() -> None:
    """metadata final URL 應沿用 route detection 支援的 Facebook host。"""

    canonical_url = "https://www.facebook.com/groups/222518561920110"

    assert final_url_matches_expected_group(
        final_url="https://mbasic.facebook.com/groups/222518561920110?ref=share",
        canonical_url=canonical_url,
    )
    assert not final_url_matches_expected_group(
        final_url="https://example.test/groups/222518561920110",
        canonical_url=canonical_url,
    )
    assert not final_url_matches_expected_group(
        final_url="https://www.facebook.com/groups/999999999999999",
        canonical_url=canonical_url,
    )
