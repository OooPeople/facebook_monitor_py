"""Web UI form models。

職責：集中 HTML form 欄位解析與 application request 轉換，避免 route
各自手寫 keyword、checkbox 與 notification 欄位語義。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.application.services import UpsertCommentsTargetRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.application.services import UpdateTargetConfigRequest
from facebook_monitor.core.defaults import PYTHON_TARGET_CONFIG_DEFAULTS
from facebook_monitor.core.refresh_policy import MIN_REFRESH_SECONDS
from facebook_monitor.facebook.collection_policy import clamp_target_post_count


FIXED_REFRESH_MODE = "fixed"
FLOATING_REFRESH_MODE = "floating"


def parse_keywords_text(text: str) -> tuple[str, ...]:
    """將表單 keyword 文字轉成去重 tuple。"""

    keywords: list[str] = []
    for raw_item in text.replace("\n", ",").split(","):
        keyword = raw_item.strip()
        if keyword:
            keywords.append(keyword)
    return tuple(dict.fromkeys(keywords))


def checkbox_checked(value: str | None) -> bool:
    """解析 HTML checkbox 欄位。"""

    return value == "on"


def normalize_refresh_seconds(value: int, fallback: int) -> int:
    """整理 refresh 秒數，對齊 JS 版至少 5 秒的保護。"""

    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = int(fallback)
    return max(seconds, MIN_REFRESH_SECONDS)


@dataclass(frozen=True)
class TargetConfigForm:
    """保存 target create/update 共用設定欄位。"""

    include_keywords: str = ""
    exclude_keywords: str = ""
    refresh_mode: str = FIXED_REFRESH_MODE
    fixed_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec
    min_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec
    max_refresh_sec: int = PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec
    max_items_per_scan: int = 3
    auto_load_more: str | None = None
    auto_adjust_sort: str | None = None
    enable_desktop_notification: str | None = None
    enable_ntfy: str | None = None
    ntfy_topic: str = ""
    enable_discord_notification: str | None = None
    discord_webhook: str = ""

    @property
    def include_keyword_tuple(self) -> tuple[str, ...]:
        """回傳已解析 include keywords。"""

        return parse_keywords_text(self.include_keywords)

    @property
    def exclude_keyword_tuple(self) -> tuple[str, ...]:
        """回傳已解析 exclude keywords。"""

        return parse_keywords_text(self.exclude_keywords)

    @property
    def normalized_refresh_mode(self) -> str:
        """回傳目前使用的 refresh mode。"""

        mode = self.refresh_mode.strip().lower()
        if mode in {FIXED_REFRESH_MODE, FLOATING_REFRESH_MODE}:
            return mode
        raise ValueError("刷新模式必須是 fixed 或 floating")

    @property
    def normalized_fixed_refresh_sec(self) -> int:
        """回傳至少 5 秒的固定掃描間隔。"""

        return normalize_refresh_seconds(
            self.fixed_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.fixed_refresh_sec,
        )

    @property
    def normalized_min_refresh_sec(self) -> int:
        """回傳至少 5 秒的浮動最小掃描間隔。"""

        return normalize_refresh_seconds(
            self.min_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.min_refresh_sec,
        )

    @property
    def normalized_max_refresh_sec(self) -> int:
        """回傳至少 5 秒的浮動最大掃描間隔。"""

        return normalize_refresh_seconds(
            self.max_refresh_sec,
            PYTHON_TARGET_CONFIG_DEFAULTS.max_refresh_sec,
        )

    @property
    def refresh_fixed_value(self) -> int | None:
        """依 refresh mode 回傳 fixed_refresh_sec 寫入值。"""

        if self.normalized_refresh_mode == FLOATING_REFRESH_MODE:
            return None
        return self.normalized_fixed_refresh_sec

    @property
    def refresh_jitter_enabled(self) -> bool:
        """依 refresh mode 回傳 jitter_enabled 寫入值。"""

        return self.normalized_refresh_mode == FLOATING_REFRESH_MODE

    def validate_refresh_range(self) -> None:
        """確認浮動刷新範圍合法。"""

        if self.normalized_refresh_mode != FLOATING_REFRESH_MODE:
            return
        if self.normalized_min_refresh_sec > self.normalized_max_refresh_sec:
            raise ValueError("浮動刷新最小秒數不可大於最大秒數")

    @property
    def normalized_max_items_per_scan(self) -> int:
        """回傳符合 domain policy 的每輪掃描上限。"""

        return clamp_target_post_count(self.max_items_per_scan)

    def to_update_request(self, *, target_id: str) -> UpdateTargetConfigRequest:
        """轉成更新 target group config 的 application request。"""

        self.validate_refresh_range()
        return UpdateTargetConfigRequest(
            target_id=target_id,
            include_keywords=self.include_keyword_tuple,
            exclude_keywords=self.exclude_keyword_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            enable_desktop_notification=checkbox_checked(self.enable_desktop_notification),
            enable_ntfy=checkbox_checked(self.enable_ntfy),
            ntfy_topic=self.ntfy_topic.strip(),
            enable_discord_notification=checkbox_checked(self.enable_discord_notification),
            discord_webhook=self.discord_webhook.strip(),
        )

    def to_group_posts_upsert_request(
        self,
        *,
        group_id: str,
        canonical_url: str,
        name: str,
        group_name: str,
    ) -> UpsertGroupPostsTargetRequest:
        """轉成 posts target upsert request。"""

        self.validate_refresh_range()
        return UpsertGroupPostsTargetRequest(
            group_id=group_id,
            canonical_url=canonical_url,
            name=name,
            group_name=group_name,
            include_keywords=self.include_keyword_tuple,
            exclude_keywords=self.exclude_keyword_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            enable_desktop_notification=checkbox_checked(self.enable_desktop_notification),
            enable_ntfy=checkbox_checked(self.enable_ntfy),
            ntfy_topic=self.ntfy_topic.strip(),
            enable_discord_notification=checkbox_checked(self.enable_discord_notification),
            discord_webhook=self.discord_webhook.strip(),
        )

    def to_comments_upsert_request(
        self,
        *,
        group_id: str,
        parent_post_id: str,
        canonical_url: str,
        name: str,
        group_name: str,
    ) -> UpsertCommentsTargetRequest:
        """轉成 comments target upsert request。"""

        self.validate_refresh_range()
        return UpsertCommentsTargetRequest(
            group_id=group_id,
            parent_post_id=parent_post_id,
            canonical_url=canonical_url,
            name=name,
            group_name=group_name,
            include_keywords=self.include_keyword_tuple,
            exclude_keywords=self.exclude_keyword_tuple,
            fixed_refresh_sec=self.refresh_fixed_value,
            min_refresh_sec=self.normalized_min_refresh_sec,
            max_refresh_sec=self.normalized_max_refresh_sec,
            jitter_enabled=self.refresh_jitter_enabled,
            max_items_per_scan=self.normalized_max_items_per_scan,
            auto_load_more=checkbox_checked(self.auto_load_more),
            auto_adjust_sort=checkbox_checked(self.auto_adjust_sort),
            enable_desktop_notification=checkbox_checked(self.enable_desktop_notification),
            enable_ntfy=checkbox_checked(self.enable_ntfy),
            ntfy_topic=self.ntfy_topic.strip(),
            enable_discord_notification=checkbox_checked(self.enable_discord_notification),
            discord_webhook=self.discord_webhook.strip(),
        )
