"""Phase 0 通知 helper 相容入口。

職責：保留舊 probe import 路徑，實作已搬到正式 package。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from facebook_monitor.notifications.ntfy import NtfyConfig
from facebook_monitor.notifications.ntfy import NtfyResult
from facebook_monitor.notifications.ntfy import send_ntfy_notification

__all__ = [
    "NtfyConfig",
    "NtfyResult",
    "send_ntfy_notification",
]
