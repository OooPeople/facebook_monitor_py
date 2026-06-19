"""Keyword match notification message builders。"""

from __future__ import annotations

from facebook_monitor.application.target_display import format_target_display_name
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.desktop_format import build_compact_notification_body
from facebook_monitor.notifications.discord_format import build_discord_match_notification_payload
from facebook_monitor.notifications.ntfy_format import build_ntfy_match_notification_payload
from facebook_monitor.notifications.payload import MatchNotificationFields


def build_ntfy_match_notification_message(
    *,
    target: TargetDescriptor,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    item_kind: ItemKind = ItemKind.POST,
) -> tuple[str, str]:
    """建立 ntfy / plain-text keyword match 通知標題與內容。"""

    return build_ntfy_match_notification_payload(
        build_match_notification_fields(
            target=target,
            item_kind=item_kind,
            author=author,
            item_text=item_text,
            permalink=permalink,
            matched_keyword=matched_keyword,
        )
    )


def build_match_discord_notification_message(
    *,
    target: TargetDescriptor,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    item_kind: ItemKind = ItemKind.POST,
) -> tuple[str, str]:
    """建立 Discord 專用 keyword match 通知標題與內容。"""

    return build_discord_match_notification_payload(
        build_match_notification_fields(
            target=target,
            item_kind=item_kind,
            author=author,
            item_text=item_text,
            permalink=permalink,
            matched_keyword=matched_keyword,
        )
    )


def build_match_compact_notification_message(
    *,
    target: TargetDescriptor,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    item_kind: ItemKind = ItemKind.POST,
) -> str:
    """建立桌面通知使用的短內容。"""

    return build_compact_notification_body(
        build_match_notification_fields(
            target=target,
            item_kind=item_kind,
            author=author,
            item_text=item_text,
            permalink=permalink,
            matched_keyword=matched_keyword,
        )
    )


def build_match_notification_fields(
    *,
    target: TargetDescriptor,
    author: str,
    item_text: str,
    permalink: str,
    matched_keyword: str,
    item_kind: ItemKind = ItemKind.POST,
) -> MatchNotificationFields:
    """依 target 顯示語義建立各通道 formatter 共用的 match 欄位。"""

    return MatchNotificationFields(
        group_name=format_target_display_name(target),
        item_kind=item_kind.value,
        author=author,
        include_rule=matched_keyword,
        text=item_text,
        permalink=permalink,
    )
