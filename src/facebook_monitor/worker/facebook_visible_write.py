"""Facebook-derived normal write 的 process/DB generation fence。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.core.facebook_access import FacebookAdmissionToken
from facebook_monitor.worker.facebook_automation_admission import (
    FacebookAutomationAdmissionController,
)
from facebook_monitor.worker.scan_commit_guard import begin_scan_commit_transaction


class FacebookVisibleWriteRejected(RuntimeError):
    """表示 circuit epoch/generation 已變更，normal result 不得寫回。"""


@contextmanager
def fenced_facebook_application_context(
    *,
    db_path: Path,
    controller: FacebookAutomationAdmissionController | None,
    token: FacebookAdmissionToken | None,
) -> Iterator[ApplicationContext]:
    """讓 DB commit 在 process write fence 內完成，legacy caller 則維持原契約。"""

    if controller is None or token is None:
        with SqliteApplicationContext(db_path) as app:
            yield app
        return

    with controller.normal_visible_write_fence(token) as process_current:
        if not process_current:
            raise FacebookVisibleWriteRejected("facebook process safety epoch changed")
        with SqliteApplicationContext(db_path) as app:
            begin_scan_commit_transaction(app)
            if not controller.db_admission_is_current(app, token):
                raise FacebookVisibleWriteRejected("facebook circuit generation changed")
            yield app


__all__ = [
    "FacebookVisibleWriteRejected",
    "fenced_facebook_application_context",
]
