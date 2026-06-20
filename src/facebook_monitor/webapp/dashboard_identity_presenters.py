"""Dashboard target identity presenter。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.application.target_display import format_target_display_name
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetMetadataStatus


PENDING_TARGET_DISPLAY_NAME = "抓取社團名稱中，請稍後"
FAILED_TARGET_DISPLAY_NAME = "無法自動抓取名稱，請手動更改名稱"


@dataclass(frozen=True)
class TargetIdentityPresenter:
    """整理 target 顯示名稱與類型 label。"""

    target: TargetDescriptor

    @property
    def display_name(self) -> str:
        """回傳 UI 顯示名稱。"""

        return format_target_display_name(
            self.target,
            generated_fallback=self._metadata_fallback_display_name(),
        )

    @property
    def rename_value(self) -> str:
        """回傳更名 modal 預填值；metadata 未完成時不回填系統 fallback。"""

        display_name = self.display_name
        if display_name in {PENDING_TARGET_DISPLAY_NAME, FAILED_TARGET_DISPLAY_NAME}:
            return ""
        return display_name

    def _metadata_fallback_display_name(self) -> str:
        """依 target metadata 狀態顯示 fallback 名稱文案。"""

        if self.target.metadata_status == TargetMetadataStatus.FAILED:
            return FAILED_TARGET_DISPLAY_NAME
        return PENDING_TARGET_DISPLAY_NAME
