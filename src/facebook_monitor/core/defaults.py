"""Python 版監視設定預設值。

職責：集中保存 Python 版刻意採用的預設值，避免 Web UI、service 與
domain model 各自硬寫一份而與 JS 移植語義漂移。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetConfigDefaults:
    """保存 target/group config 的 Python 版預設值。"""

    fixed_refresh_sec: int = 60
    min_refresh_sec: int = 25
    max_refresh_sec: int = 35
    jitter_enabled: bool = True
    max_items_per_scan: int = 5
    auto_load_more: bool = True
    auto_adjust_sort: bool = True
    enable_desktop_notification: bool = False
    enable_ntfy: bool = False
    ntfy_topic: str = ""
    enable_discord_notification: bool = False
    discord_webhook: str = ""


PYTHON_TARGET_CONFIG_DEFAULTS = TargetConfigDefaults()
