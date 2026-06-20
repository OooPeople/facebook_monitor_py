"""Dashboard target settings presenters。"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.keyword_groups import legacy_include_keyword_groups
from facebook_monitor.core.keyword_groups import normalize_include_keyword_groups
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.notification_channels import NOTIFICATION_CHANNEL_DEFINITIONS
from facebook_monitor.webapp.form_refresh import FIXED_REFRESH_MODE
from facebook_monitor.webapp.form_refresh import FLOATING_REFRESH_MODE
from facebook_monitor.webapp.notification_presenters import format_notification_channel_label


EMPTY_INCLUDE_KEYWORDS_LABEL = "目前沒有關鍵字"


@dataclass(frozen=True)
class SettingsSummaryLine:
    """保存設定摘要單列結構，避免 template 用字串切割推論欄位。"""

    icon_key: str
    label: str
    value: str


@dataclass(frozen=True)
class SettingsSummary:
    """保存 target card 設定摘要。"""

    lines: tuple[SettingsSummaryLine, ...]

    @property
    def max_items_label(self) -> str:
        """回傳收合摘要沿用的目標掃描數。"""

        for line in self.lines:
            if line.icon_key == "target":
                return line.value
        return ""


@dataclass(frozen=True)
class TargetSettingsPresenter:
    """整理 target 設定值的表單文字與摘要。"""

    config: TargetConfig

    @property
    def include_text(self) -> str:
        """回傳 include keywords 表單文字。"""

        return self.include_group_texts[0] if self.include_group_texts else ""

    @property
    def include_text_2(self) -> str:
        """回傳 include keyword 第 2 組表單文字。"""

        return self.include_group_texts[1] if len(self.include_group_texts) > 1 else ""

    @property
    def include_text_3(self) -> str:
        """回傳 include keyword 第 3 組表單文字。"""

        return self.include_group_texts[2] if len(self.include_group_texts) > 2 else ""

    @property
    def include_group_texts(self) -> tuple[str, ...]:
        """回傳固定 include group slots 的表單文字。"""

        groups = normalize_include_keyword_groups(
            self.config.include_keyword_groups,
            fill_empty_slots=True,
        )
        if not any(group.keywords for group in groups) and self.config.include_keywords:
            groups = legacy_include_keyword_groups(
                self.config.include_keywords,
                fill_empty_slots=True,
            )
        return tuple(";".join(group.keywords) for group in groups)

    @property
    def include_summary_label(self) -> str:
        """回傳收合摘要使用的 include keyword 短文字。"""

        non_empty_groups = [text for text in self.include_group_texts if text]
        if not non_empty_groups:
            return EMPTY_INCLUDE_KEYWORDS_LABEL
        keyword_count = sum(
            len(tuple(keyword for keyword in text.split(";") if keyword.strip()))
            for text in non_empty_groups
        )
        if len(non_empty_groups) == 1:
            return non_empty_groups[0]
        return f"{len(non_empty_groups)} 組 / {keyword_count} 條"

    @property
    def exclude_text(self) -> str:
        """回傳 exclude keywords 表單文字。"""

        return ";".join(self.config.exclude_keywords)

    @property
    def exclude_ignore_phrases_text(self) -> str:
        """回傳排除字忽略片語表單文字。"""

        return ";".join(self.config.exclude_ignore_phrases)

    @property
    def fixed_refresh_value(self) -> int:
        """回傳表單使用的固定掃描間隔秒數。"""

        return (
            self.config.fixed_refresh_sec
            or PYTHON_TARGET_CONFIG_DEFAULTS.default_fixed_refresh_sec
        )

    @property
    def min_refresh_value(self) -> int:
        """回傳表單使用的浮動最小掃描間隔秒數。"""

        return self.config.min_refresh_sec or PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec

    @property
    def max_refresh_value(self) -> int:
        """回傳表單使用的浮動最大掃描間隔秒數。"""

        return self.config.max_refresh_sec or PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec

    @property
    def refresh_mode(self) -> str:
        """回傳目前 refresh mode。"""

        if self.config.fixed_refresh_sec is None and self.config.jitter_enabled:
            return FLOATING_REFRESH_MODE
        return FIXED_REFRESH_MODE

    @property
    def refresh_mode_label(self) -> str:
        """回傳 refresh mode 摘要。"""

        if self.refresh_mode == FLOATING_REFRESH_MODE:
            min_seconds = min(self.config.min_refresh_sec, self.config.max_refresh_sec)
            max_seconds = max(self.config.min_refresh_sec, self.config.max_refresh_sec)
            return f"浮動 {min_seconds}-{max_seconds} 秒"
        return f"固定 {self.fixed_refresh_value} 秒"

    @property
    def notification_summary_label(self) -> str:
        """回傳設定摘要用的通知通道列表。"""

        channels = [
            format_notification_channel_label(definition.channel)
            for definition in NOTIFICATION_CHANNEL_DEFINITIONS
            if getattr(self.config, definition.enabled_field)
        ]
        return " / ".join(channels) if channels else "關閉"

    @property
    def settings_summary(self) -> SettingsSummary:
        """回傳 target card 設定摘要 view model。"""

        return SettingsSummary(
            lines=(
                SettingsSummaryLine("refresh", "刷新", self.refresh_mode_label),
                SettingsSummaryLine(
                    "target",
                    "目標掃描",
                    f"{self.config.max_items_per_scan} 筆",
                ),
                SettingsSummaryLine(
                    "load_more",
                    "載入更多",
                    "開啟" if self.config.auto_load_more else "關閉",
                ),
                SettingsSummaryLine(
                    "sort",
                    "最新排序",
                    "開啟" if self.config.auto_adjust_sort else "關閉",
                ),
                SettingsSummaryLine("notification", "通知", self.notification_summary_label),
            ),
        )
