"""完整命中紀錄 view models。

職責：整理查看紀錄 modal 與 hit-record API 使用的詳細資料。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.webapp.highlight import build_highlight_segment_dicts
from facebook_monitor.webapp.notification_presenters import format_notification_channel_label
from facebook_monitor.webapp.notification_presenters import format_notification_event_message
from facebook_monitor.webapp.notification_presenters import format_notification_status_label
from facebook_monitor.webapp.preview_models import trim_preview_text
from facebook_monitor.webapp.url_safety import safe_facebook_permalink


@dataclass(frozen=True)
class FullHitRecordRow:
    """保存完整命中紀錄 modal 使用的詳細資料。"""

    entry: MatchHistoryEntry
    sequence_number: int
    notification_event: NotificationEvent | None = None

    @property
    def item_type(self) -> str:
        """回傳貼文 / 留言類型文字。"""

        return "留言" if self.entry.item_kind == ItemKind.COMMENT else "貼文"

    @property
    def matched_at(self) -> str:
        """回傳命中紀錄建立時間。"""

        return format_datetime_for_ui(self.entry.created_at)

    @property
    def recorded_at(self) -> str:
        """回傳命中紀錄寫入時間。"""

        recorded_at = self.entry.notified_at or self.entry.created_at
        return format_datetime_for_ui(recorded_at)

    @property
    def notification_summary(self) -> str:
        """回傳完整紀錄使用的通知摘要。"""

        if self.notification_event is None:
            return "尚無通知事件"
        event = self.notification_event
        event_time = format_datetime_for_ui(event.created_at)
        message = (
            f" · {format_notification_event_message(event.message)}"
            if event.message
            else ""
        )
        channel_label = format_notification_channel_label(event.channel)
        status_label = format_notification_status_label(event.status)
        return f"{channel_label}: {status_label} · {event_time}{message}"

    def to_dict(self) -> dict[str, object]:
        """轉成 API response 使用的純 dict。"""

        return {
            "record_id": self.entry.item_key,
            "sequence_number": self.sequence_number,
            "target_id": self.entry.target_id,
            "item_type": self.item_type,
            "author_name": self.entry.author or "(unknown)",
            "matched_keyword": self.entry.include_rule,
            "matched_keywords": list(self.entry.include_rules),
            "matched_keyword_groups": [
                {
                    "group_id": match.group_id,
                    "group_label": match.group_label,
                    "rule": match.rule,
                }
                for match in self.entry.include_group_matches
            ],
            "matched_at": self.matched_at,
            "recorded_at": self.recorded_at,
            "notified_at": self.recorded_at,
            "notification_summary": self.notification_summary,
            "content": self.entry.text,
            "content_segments": build_highlight_segment_dicts(
                self.entry.text,
                self.entry.include_rule,
            ),
            "content_preview": trim_preview_text(self.entry.text, max_length=220),
            "permalink": safe_facebook_permalink(self.entry.permalink),
        }
