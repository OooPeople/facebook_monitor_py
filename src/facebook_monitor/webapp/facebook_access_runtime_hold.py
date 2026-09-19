"""Facebook automation runtime hold 的 Web compatibility facade。"""

from __future__ import annotations

from facebook_monitor.application.facebook_automation_runtime_observability import (
    read_facebook_automation_runtime_hold,
)
from facebook_monitor.application.facebook_automation_runtime_observability import (
    STORAGE_CRITICAL_HOLD,
)
from facebook_monitor.application.facebook_automation_runtime_observability import (
    UNCLEAN_SESSION_HOLD,
)


__all__ = [
    "STORAGE_CRITICAL_HOLD",
    "UNCLEAN_SESSION_HOLD",
    "read_facebook_automation_runtime_hold",
]
