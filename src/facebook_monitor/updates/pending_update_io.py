"""Pending update JSON file IO。

職責：讀取 pending update JSON，並套用 schema、payload 與 artifact 驗證。
"""

from __future__ import annotations

import json
from pathlib import Path

from facebook_monitor.updates.pending_update_codec import pending_update_from_payload
from facebook_monitor.updates.pending_update_models import PendingUpdate
from facebook_monitor.updates.pending_update_validation import (
    validate_pending_update_artifact_set,
)
from facebook_monitor.updates.pending_update_validation import validate_pending_update_paths
from facebook_monitor.updates.pending_update_validation import (
    validate_pending_update_payload_integrity,
)


def load_pending_update(path: Path) -> PendingUpdate:
    """讀取並驗證 pending update JSON。"""

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    pending = pending_update_from_payload(payload)
    validate_pending_update_payload_integrity(pending)
    validate_pending_update_paths(pending, pending_path=path)
    validate_pending_update_artifact_set(pending)
    return pending


__all__ = ["load_pending_update"]
