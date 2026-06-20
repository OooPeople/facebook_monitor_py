"""Target metadata normalization policy。

職責：集中 Facebook-derived metadata 的清理、拒絕與既有污染值處理規則。
"""

from __future__ import annotations

from facebook_monitor.application.target_display import clean_target_display_name
from facebook_monitor.core.external_url_policy import sanitize_facebook_group_cover_image_url
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.facebook.group_metadata_validation import (
    has_polluted_group_cover_image_url,
)
from facebook_monitor.facebook.group_metadata_validation import is_invalid_facebook_group_name


class InvalidTargetMetadataError(ValueError):
    """表示 Facebook-derived metadata 不應寫入 target。"""


def clean_persisted_target_name(value: str) -> str:
    """清理準備保存的 Facebook target 名稱。"""

    return clean_target_display_name(value)


def normalize_metadata_error(value: str) -> str:
    """把 metadata refresh 錯誤壓成可保存的短訊息。"""

    normalized = " ".join(str(value or "").split())
    if not normalized:
        return "metadata refresh failed"
    return normalized[:500]


def normalize_group_metadata_name(value: str, *, strict: bool = False) -> str:
    """整理 Facebook-derived group name；錯誤頁名稱不得進入 metadata。"""

    normalized = clean_persisted_target_name(value)
    if normalized and is_invalid_facebook_group_name(normalized):
        if strict:
            raise InvalidTargetMetadataError("Facebook 回傳錯誤頁，未更新 target metadata")
        return ""
    return normalized


def normalize_metadata_url(value: str, *, strict: bool = False) -> str:
    """整理 Facebook metadata URL，避免空白與控制字元進 DB。"""

    raw = str(value or "").strip()
    result = sanitize_facebook_group_cover_image_url(raw)
    if raw and not result.ok and strict:
        raise InvalidTargetMetadataError(
            "Facebook 回傳不可作為社團封面的圖片，未更新 target metadata"
        )
    return result.url if result.ok else ""


def existing_cover_image_url_or_empty(target: TargetDescriptor) -> str:
    """回傳既有封面 URL；已知錯誤頁通用圖視為空值。"""

    if has_polluted_group_cover_image_url(target.group_cover_image_url):
        return ""
    return target.group_cover_image_url


def next_metadata_cover_image_url(
    target: TargetDescriptor,
    request_cover_image_url: str,
) -> str:
    """決定 metadata refresh 後要保存的封面 URL，避免保留通用錯誤圖。"""

    if request_cover_image_url:
        return request_cover_image_url
    return existing_cover_image_url_or_empty(target)


__all__ = [
    "InvalidTargetMetadataError",
    "clean_persisted_target_name",
    "existing_cover_image_url_or_empty",
    "next_metadata_cover_image_url",
    "normalize_group_metadata_name",
    "normalize_metadata_error",
    "normalize_metadata_url",
]
