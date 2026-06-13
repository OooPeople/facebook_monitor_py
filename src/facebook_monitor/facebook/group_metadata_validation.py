"""Facebook group metadata 驗證規則。"""

from __future__ import annotations

from urllib.parse import urlsplit

from facebook_monitor.core.external_url_policy import is_generic_facebook_image_url
from facebook_monitor.facebook.route_detection import FACEBOOK_HOSTS
from facebook_monitor.facebook.route_detection import clean_facebook_page_title


INVALID_FACEBOOK_GROUP_NAMES = frozenset({
    "facebook | error",
})

UNAVAILABLE_BODY_MARKERS = (
    "this page isn't available",
    "this content isn't available",
    "sorry, something went wrong",
    "page isn't available right now",
    "此頁面無法使用",
    "這個內容目前無法顯示",
    "這個內容目前無法查看",
)


def is_invalid_facebook_group_name(value: object) -> bool:
    """判斷解析到的社團名稱是否明顯來自 Facebook 錯誤頁。"""

    normalized = clean_facebook_page_title(str(value or "")).casefold().strip()
    return normalized in INVALID_FACEBOOK_GROUP_NAMES


def has_polluted_group_cover_image_url(value: object) -> bool:
    """判斷 target 封面 URL 是否為已知 Facebook 通用圖。"""

    return is_generic_facebook_image_url(value)


def body_mentions_unavailable_page(value: object) -> bool:
    """判斷 body text 是否明確指向 Facebook unavailable/error page。"""

    normalized = " ".join(str(value or "").casefold().split())
    if not normalized:
        return False
    return any(marker in normalized for marker in UNAVAILABLE_BODY_MARKERS)


def final_url_matches_expected_group(
    *,
    final_url: object,
    canonical_url: object,
) -> bool:
    """確認導向後 URL 仍是同一個 Facebook group。"""

    final = str(final_url or "").strip()
    canonical = str(canonical_url or "").strip()
    if not final or not canonical:
        return True
    expected_group_id = _group_id_from_url(canonical)
    if not expected_group_id:
        return True
    try:
        parsed = urlsplit(final)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host not in FACEBOOK_HOSTS:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 2 and parts[0].casefold() == "groups" and parts[1] == expected_group_id


def _group_id_from_url(value: str) -> str:
    """從 canonical group URL 取出 groups 後方 id。"""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].casefold() != "groups":
        return ""
    return parts[1].strip()
