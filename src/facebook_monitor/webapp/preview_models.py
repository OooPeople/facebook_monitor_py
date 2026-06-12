"""Dashboard preview row view models。

職責：整理最近掃描與命中紀錄 preview tab 共用的 row 資料。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import MatchHistoryEntry
from facebook_monitor.core.keyword_rules import split_keyword_rule_text
from facebook_monitor.webapp.diagnostics_presenter import format_datetime_for_ui
from facebook_monitor.core.keyword_highlight import HighlightSegment
from facebook_monitor.core.keyword_highlight import build_highlight_segments
from facebook_monitor.webapp.url_safety import safe_facebook_permalink


def trim_preview_text(text: str, *, max_length: int) -> str:
    """將多行文字整理成單行預覽並限制長度。"""

    preview = " ".join(text.split())
    if len(preview) > max_length:
        return preview[: max_length - 3] + "..."
    return preview


@dataclass(frozen=True)
class TargetPreviewRow:
    """保存最近掃描與命中紀錄共用 preview row 資料。"""

    author_name: str
    badge_text: str
    badge_kind: str
    content_preview: str
    content_segments: tuple[HighlightSegment, ...] = ()
    permalink: str = ""
    link_label: str = "開啟連結"
    secondary_text: str = ""
    debug_summary: str = ""
    debug_text: str = ""

    def __post_init__(self) -> None:
        """清理可點擊外部連結，避免模板與 API 輸出 unsafe href。"""

        object.__setattr__(self, "permalink", safe_facebook_permalink(self.permalink))

    @property
    def has_debug(self) -> bool:
        """回傳 preview row 是否有可展開除錯資訊。"""

        return bool(self.debug_summary or self.debug_text)

    @property
    def badge_labels(self) -> tuple[str, ...]:
        """回傳 preview row 需顯示的 badge 文字，可支援多組命中。"""

        if not self.badge_text.startswith("命中: "):
            return (self.badge_text,) if self.badge_text else ()
        rules = split_keyword_rule_text(self.badge_text.removeprefix("命中: "))
        if not rules:
            return (self.badge_text,)
        return tuple(f"命中: {rule}" for rule in rules)

    def to_dict(self) -> dict[str, object]:
        """轉成 API response 使用的純 dict。"""

        return {
            "author_name": self.author_name,
            "badge_text": self.badge_text,
            "badge_labels": list(self.badge_labels),
            "badge_kind": self.badge_kind,
            "content_preview": self.content_preview,
            "content_segments": [segment.to_dict() for segment in self.content_segments],
            "permalink": self.permalink,
            "link_label": self.link_label,
            "secondary_text": self.secondary_text,
            "debug_summary": self.debug_summary,
            "debug_text": self.debug_text,
            "has_debug": self.has_debug,
        }


@dataclass(frozen=True)
class LatestScanItemRow:
    """保存 target 卡片右側最近掃描項目顯示資料。"""

    item: LatestScanItem

    @property
    def author_label(self) -> str:
        """回傳作者顯示文字。"""

        return self.item.author or "(unknown)"

    @property
    def match_label(self) -> str:
        """回傳掃描項目是否命中 keyword 的顯示文字。"""

        return f"命中: {self.item.matched_keyword}" if self.item.matched_keyword else "未命中"

    @property
    def preview_text(self) -> str:
        """回傳掃描項目內容預覽。"""

        return trim_preview_text(self.item.display_text or self.item.text, max_length=120)

    def to_preview_row(self, *, link_label: str) -> TargetPreviewRow:
        """轉成最近掃描 / 命中紀錄共用 preview row。"""

        return TargetPreviewRow(
            author_name=self.author_label,
            badge_text=self.match_label,
            badge_kind="hit" if self.item.matched_keyword else "not_hit",
            content_preview=self.preview_text,
            content_segments=build_highlight_segments(self.preview_text, self.item.matched_keyword),
            permalink=self.item.permalink,
            link_label=link_label,
        )


@dataclass(frozen=True)
class HitRecordPreviewRow:
    """保存命中紀錄 preview tab 共用 row 所需資料。"""

    entry: MatchHistoryEntry

    @property
    def author_name(self) -> str:
        """回傳命中紀錄作者顯示文字。"""

        return self.entry.author or "(unknown)"

    @property
    def badge_text(self) -> str:
        """回傳命中 keyword badge 文字。"""

        return f"命中: {self.entry.include_rule}" if self.entry.include_rule else "命中"

    @property
    def badge_kind(self) -> str:
        """回傳 preview row badge 類型。"""

        return "hit"

    @property
    def content_preview(self) -> str:
        """回傳命中紀錄內容預覽。"""

        return trim_preview_text(self.entry.display_text or self.entry.text, max_length=120)

    @property
    def permalink(self) -> str:
        """回傳命中紀錄原文連結。"""

        return self.entry.permalink

    @property
    def secondary_text(self) -> str:
        """回傳 preview row 的次要文字。"""

        return format_datetime_for_ui(self.entry.notified_at or self.entry.created_at)

    def to_preview_row(self) -> TargetPreviewRow:
        """轉成最近掃描 / 命中紀錄共用 preview row。"""

        return TargetPreviewRow(
            author_name=self.author_name,
            badge_text=self.badge_text,
            badge_kind=self.badge_kind,
            content_preview=self.content_preview,
            content_segments=build_highlight_segments(self.content_preview, self.entry.include_rule),
            permalink=self.permalink,
            link_label="開啟連結",
            secondary_text=self.secondary_text,
        )

    def to_dict(self) -> dict[str, object]:
        """轉成 API response 使用的純 dict。"""

        return self.to_preview_row().to_dict()
