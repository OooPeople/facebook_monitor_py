"""完整命中紀錄 view models。

職責：整理查看紀錄 modal 與 hit-record API 使用的詳細資料。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.webapp.preview_models import trim_preview_text


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
    def notified_at(self) -> str:
        """回傳通知時間。"""

        notified_at = self.entry.notified_at
        if notified_at is None and self.notification_event is not None:
            notified_at = self.notification_event.created_at
        if notified_at is None:
            return ""
        return format_datetime_for_ui(notified_at)

    @property
    def notification_summary(self) -> str:
        """回傳完整紀錄使用的通知摘要。"""

        if self.notified_at:
            return f"已記錄 {self.notified_at}"
        return "未記錄通知時間"

    def to_dict(self) -> dict[str, object]:
        """轉成 API response 使用的純 dict。"""

        return {
            "record_id": self.entry.item_key,
            "sequence_number": self.sequence_number,
            "target_id": self.entry.target_id,
            "item_type": self.item_type,
            "author_name": self.entry.author or "(unknown)",
            "matched_keyword": self.entry.include_rule,
            "matched_at": self.matched_at,
            "notified_at": self.notified_at,
            "notification_summary": self.notification_summary,
            "content": self.entry.text,
            "content_preview": trim_preview_text(self.entry.text, max_length=220),
            "permalink": self.entry.permalink,
        }
