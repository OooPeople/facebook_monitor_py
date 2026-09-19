"""同步 fallback browser lifetime 與 durable marker 契約測試。"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pytest import MonkeyPatch

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.worker.fallback_automation_admission import (
    governed_fallback_posts_work,
)


class _FakeLease:
    """記錄 fallback context manager 是否永遠釋放 admission lease。"""

    def __init__(self) -> None:
        self.release_count = 0
        self.process_lease = SimpleNamespace(operation_id="operation-1")
        self.admission_token = SimpleNamespace()

    async def release(self) -> None:
        """記錄一次冪等測試釋放。"""

        self.release_count += 1


class _FakeController:
    """提供 fallback context manager 所需的最小 admission controller。"""

    def __init__(self) -> None:
        self.lease = _FakeLease()
        self.start_count = 0
        self.finish_count = 0

    async def recover_expired_pacing_lease(self) -> None:
        """測試不需 persistent pacing recovery。"""

    async def acquire(self, **_kwargs: Any) -> Any:
        """回傳已允許的 fake governed lease。"""

        return SimpleNamespace(admitted=True, lease=self.lease, reason="")

    def start_session_guard_before_browser_io(self) -> None:
        """記錄 durable normal marker 已建立。"""

        self.start_count += 1

    def finish_session_guard_after_browser_context_closed(self) -> None:
        """記錄 durable marker 已在 close acknowledgement 後清除。"""

        self.finish_count += 1


def _patch_fallback_preflight(monkeypatch: MonkeyPatch, controller: _FakeController) -> None:
    """隔離 identity/restart guard，讓測試只驗 browser close acknowledgement。"""

    monkeypatch.setattr(
        "facebook_monitor.worker.fallback_automation_admission."
        "resolve_managed_profile_identity",
        lambda **_kwargs: SimpleNamespace(profile_scope_key="profile-scope"),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.fallback_automation_admission."
        "reconcile_facebook_automation_restart_guard",
        lambda **_kwargs: SimpleNamespace(browser_io_allowed=True, reason_code=""),
    )
    monkeypatch.setattr(
        "facebook_monitor.worker.fallback_automation_admission._build_controller",
        lambda **_kwargs: controller,
    )


def test_fallback_marker_is_retained_without_context_close_acknowledgement(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """browser launch/close 不確定時仍釋放 lease，但不可清 durable marker。"""

    controller = _FakeController()
    _patch_fallback_preflight(monkeypatch, controller)
    profile_dir = tmp_path / "profiles" / "managed"
    profile_dir.mkdir(parents=True)

    with governed_fallback_posts_work(
        db_path=tmp_path / "app.db",
        profile_dir=profile_dir,
        profile_lease_factory=lambda *_args: nullcontext(),
        profile_lease_owner="test",
        owner_alias="test",
    ):
        pass

    assert controller.start_count == 1
    assert controller.finish_count == 0
    assert controller.lease.release_count == 1


def test_fallback_marker_is_cleared_after_context_close_acknowledgement(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """caller 明確確認 browser context close 後才可清 durable marker。"""

    controller = _FakeController()
    _patch_fallback_preflight(monkeypatch, controller)
    profile_dir = tmp_path / "profiles" / "managed"
    profile_dir.mkdir(parents=True)

    with governed_fallback_posts_work(
        db_path=tmp_path / "app.db",
        profile_dir=profile_dir,
        profile_lease_factory=lambda *_args: nullcontext(),
        profile_lease_owner="test",
        owner_alias="test",
    ) as work:
        work.note_browser_context_closed()

    assert controller.start_count == 1
    assert controller.finish_count == 1
    assert controller.lease.release_count == 1


def test_fallback_marker_and_pacing_share_canonical_session_owner(tmp_path: Path) -> None:
    """Fallback在acquire後建立的marker必須與pacing共用canonical UUID。"""

    db_path = tmp_path / "app.db"
    profile_dir = tmp_path / "profiles" / "managed"
    profile_dir.mkdir(parents=True)

    with governed_fallback_posts_work(
        db_path=db_path,
        profile_dir=profile_dir,
        profile_lease_factory=lambda *_args: nullcontext(),
        profile_lease_owner="test",
        owner_alias="test",
    ) as work:
        session_guard_runtime = work.controller.session_guard_runtime
        assert session_guard_runtime is not None
        marker = session_guard_runtime.store.read()
        with SqliteApplicationContext(db_path) as app:
            pacing = app.repositories.facebook_automation_pacing.get(
                work.profile_scope_key
            )
        assert marker is not None
        assert pacing is not None
        assert str(UUID(marker.session_id)) == marker.session_id
        assert pacing.owner_session_id == marker.session_id
        work.note_browser_context_closed()
