"""Debug helper：重新匯出 ntfy probe 需要的正式通知 API。"""

# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
