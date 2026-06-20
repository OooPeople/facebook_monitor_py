"""TargetRow preview panel presenter。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.models import TargetKind
from facebook_monitor.webapp.preview_models import HitRecordPreviewRow
from facebook_monitor.webapp.preview_models import LatestScanItemRow
from facebook_monitor.webapp.preview_models import TargetPreviewRow


@dataclass(frozen=True)
class TargetPreviewPresenter:
    """整理 target card 右側 preview panel 欄位。"""

    target_kind: TargetKind
    latest_scan_items: tuple[LatestScanItemRow, ...]
    hit_record_preview_items: tuple[HitRecordPreviewRow, ...]
    hit_record_total_count: int

    @property
    def latest_items_heading(self) -> str:
        """回傳最近掃描項目的標題。"""

        label = "留言" if self.target_kind == TargetKind.COMMENTS else "貼文"
        return f"最近掃描{label}（{len(self.latest_scan_items)}）"

    @property
    def latest_item_link_label(self) -> str:
        """回傳最近掃描項目 permalink 的連結文字。"""

        return "開啟連結"

    @property
    def latest_scan_preview_rows(self) -> tuple[TargetPreviewRow, ...]:
        """回傳最近掃描 preview rows。"""

        return tuple(
            item.to_preview_row(link_label=self.latest_item_link_label)
            for item in self.latest_scan_items
        )

    @property
    def hit_record_preview_rows(self) -> tuple[TargetPreviewRow, ...]:
        """回傳命中紀錄 preview rows。"""

        return tuple(item.to_preview_row() for item in self.hit_record_preview_items)

    @property
    def hit_records_heading(self) -> str:
        """回傳命中紀錄 preview 標題。"""

        return f"命中紀錄（{self.hit_record_total_count}）"
