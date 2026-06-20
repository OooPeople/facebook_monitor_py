"""Resident main worker tests。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from datetime import timedelta
from pathlib import Path
import sqlite3
from typing import Any

from pytest import MonkeyPatch

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationOutboxEntry
from facebook_monitor.core.models import NotificationOutboxStatus
from facebook_monitor.core.models import TargetCoverImageRefreshStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetMetadataStatus
from facebook_monitor.core.models import TargetRuntimeStatus
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import PAGE_LOAD_TIMEOUT_REASON
from facebook_monitor.notifications.outbox_dispatch_models import (
    PendingNotificationOutboxDispatchResult,
)
from facebook_monitor.notifications.outbox_dispatcher import NotificationOutboxDispatcher
from facebook_monitor.notifications.outbox_dispatcher import (
    register_notification_outbox_dispatcher,
)
from facebook_monitor.notifications.outbox_dispatcher import (
    unregister_notification_outbox_dispatcher,
)
from facebook_monitor.scheduler.planner import TargetSchedulePlanner
from facebook_monitor.persistence.sqlite_codec import encode_datetime
from facebook_monitor.worker.resident_main import dispatch_pending_notification_outbox
from facebook_monitor.worker.resident_cover_image_refresh import (
    refresh_pending_target_cover_images,
)
from facebook_monitor.worker.resident_cover_image_refresh import (
    refresh_target_group_cover_image_from_context,
)
from facebook_monitor.worker.resident_metadata_refresh import (
    METADATA_REFRESH_TARGET_LIMIT_PER_TICK,
)
from facebook_monitor.worker.resident_metadata_refresh import (
    refresh_requested_target_metadata,
)
from facebook_monitor.worker.resident_main import run_bounded_retention_maintenance_if_due
from facebook_monitor.worker.resident_main import run_resident_main_scheduler_tick
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.resident_main_executor import ExecutorWorkerPool
from facebook_monitor.worker.resident_main_page_pool import AsyncResidentPagePool
from facebook_monitor.worker.resident_main_queue import TargetQueue
from facebook_monitor.worker.resident_shared import ResidentRuntimeOptions


from tests.worker.resident_main_test_helpers import FakeAsyncBrowserContext
from tests.worker.resident_main_test_helpers import FakeMetadataBrowserContext
from tests.worker.resident_main_test_helpers import FakeMetadataLocator
from tests.worker.resident_main_test_helpers import FakeMetadataPage
from tests.worker.resident_main_test_helpers import FakeLoggedOutMetadataBrowserContext
from tests.worker.resident_main_test_helpers import FakeShutdownMetadataBrowserContext
from tests.worker.resident_main_test_helpers import as_async_scan_callable
from tests.worker.resident_main_test_helpers import build_success_scan_result_for_test


class FakeUnavailableMetadataLocator(FakeMetadataLocator):
    """Facebook outage/error page 測試用 locator。"""

    async def inner_text(self, *, timeout: int) -> str:
        """回傳 Facebook 錯誤頁 body text。"""

        return "This page isn't available right now"


class FakeUnavailableMetadataPage(FakeMetadataPage):
    """metadata repair 測試用 Facebook 錯誤頁。"""

    def locator(self, selector: str) -> FakeUnavailableMetadataLocator:
        """回傳錯誤頁 body locator。"""

        return FakeUnavailableMetadataLocator()

    async def title(self) -> str:
        """回傳 outage 期間觀察到的錯誤頁 title。"""

        return "Facebook | Error"

    async def evaluate(self, script: str) -> str:
        """回傳 outage 期間觀察到的 Facebook 通用 logo。"""

        return "https://static.facebook.com/images/logos/facebook_2x.png"


class FakeUnavailableMetadataBrowserContext:
    """metadata repair 測試用 Facebook 錯誤頁 context。"""

    def __init__(self) -> None:
        self.pages: list[FakeUnavailableMetadataPage] = []

    async def new_page(self) -> FakeUnavailableMetadataPage:
        """建立錯誤頁 fake page。"""

        page = FakeUnavailableMetadataPage()
        self.pages.append(page)
        return page


class FakeNoCoverMetadataPage(FakeMetadataPage):
    """cover image refresh 測試用：頁面可開啟但沒有可用封面。"""

    async def evaluate(self, script: str) -> str:
        """回傳空封面 URL。"""

        return ""


class FakeNoCoverMetadataBrowserContext:
    """cover image refresh 測試用無封面 context。"""

    def __init__(self) -> None:
        self.pages: list[FakeNoCoverMetadataPage] = []

    async def new_page(self) -> FakeNoCoverMetadataPage:
        """建立無封面 fake page。"""

        page = FakeNoCoverMetadataPage()
        self.pages.append(page)
        return page


def test_resident_scheduler_tick_refreshes_requested_target_metadata(tmp_path: Path) -> None:
    """resident scheduler 會用既有 browser context 補齊 fallback target name。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("metadata refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
                metadata_refresh_provider=lambda: (target.id,),
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 1
        assert summary.metadata_refresh_count == 1
        assert summary.cover_image_refresh_count == 0
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].url == "https://www.facebook.com/groups/222518561920110"
        assert metadata_context.pages[0].closed

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""


def test_resident_scheduler_tick_repairs_polluted_metadata_during_maintenance(
    tmp_path: Path,
) -> None:
    """polluted metadata 會被既有 maintenance tick 修復，不掛在 due enqueue 前。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="Facebook | Error",
                group_name="Facebook | Error",
                group_cover_image_url=("https://static.facebook.com/images/logos/facebook_2x.png"),
            )
        )

    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeMetadataBrowserContext(),
        )
    )

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert refreshed_count == 1
    assert updated is not None
    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""


def test_polluted_metadata_refresh_preserves_custom_target_name(
    tmp_path: Path,
) -> None:
    """自動 metadata repair 只修 Facebook group metadata，不覆蓋使用者自訂名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
            )
        )
        app.repositories.targets.save(replace(target, group_name="Facebook | Error"))

    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeMetadataBrowserContext(),
        )
    )

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert refreshed_count == 1
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"


def test_pending_metadata_refresh_takes_priority_over_polluted_candidate(
    tmp_path: Path,
) -> None:
    """metadata pending 會先吃掉每 tick limit，polluted auto candidate 等下一輪。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        pending = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="pending",
                canonical_url="https://www.facebook.com/groups/pending",
            )
        )
        polluted = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="polluted",
                canonical_url="https://www.facebook.com/groups/polluted",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(pending.id)
        app.repositories.targets.save(
            replace(
                polluted,
                name="Facebook | Error",
                group_name="Facebook | Error",
            )
        )

    context = FakeMetadataBrowserContext()
    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=context,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        loaded_pending = app.repositories.targets.get(pending.id)
        loaded_polluted = app.repositories.targets.get(polluted.id)
    assert refreshed_count == 1
    assert len(context.pages) == 1
    assert loaded_pending is not None
    assert loaded_pending.metadata_status == TargetMetadataStatus.RESOLVED
    assert loaded_polluted is not None
    assert loaded_polluted.name == "Facebook | Error"


def test_pending_metadata_refresh_skips_ineligible_rows_without_starving_next_target(
    tmp_path: Path,
) -> None:
    """舊 ineligible pending target 不應擋住後續可執行 metadata refresh。"""

    db_path = tmp_path / "app.db"
    blocked_ids: list[str] = []
    with SqliteApplicationContext(db_path) as app:
        for index in range(METADATA_REFRESH_TARGET_LIMIT_PER_TICK * 4):
            blocked = app.services.targets.upsert_group_posts_target(
                UpsertGroupPostsTargetRequest(
                    group_id=f"blocked-pending-{index}",
                    canonical_url=f"https://www.facebook.com/groups/blocked-pending-{index}",
                )
            )
            app.services.targets.mark_target_metadata_refresh_pending(blocked.id)
            app.services.targets.mark_target_error(blocked.id, "terminal error")
            blocked_ids.append(blocked.id)
        eligible = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="eligible-pending",
                canonical_url="https://www.facebook.com/groups/eligible-pending",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(eligible.id)

    context = FakeMetadataBrowserContext()
    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=context,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        blocked_targets = [app.repositories.targets.get(target_id) for target_id in blocked_ids]
        loaded_eligible = app.repositories.targets.get(eligible.id)
    assert refreshed_count == 1
    assert len(context.pages) == 1
    assert all(target is not None for target in blocked_targets)
    assert all(
        target.metadata_status == TargetMetadataStatus.PENDING
        for target in blocked_targets
        if target is not None
    )
    assert loaded_eligible is not None
    assert loaded_eligible.metadata_status == TargetMetadataStatus.RESOLVED
    assert loaded_eligible.name == "測試社團"


def test_failed_polluted_metadata_refresh_is_throttled(
    tmp_path: Path,
) -> None:
    """剛失敗的 polluted metadata 不應每個 maintenance tick 立刻重試。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="polluted",
                canonical_url="https://www.facebook.com/groups/polluted",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                name="Facebook | Error",
                group_name="Facebook | Error",
            )
        )
        app.services.targets.mark_target_metadata_refresh_failed(
            target.id,
            "Facebook 回傳錯誤頁",
        )

    context = FakeMetadataBrowserContext()
    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=context,
        )
    )

    assert refreshed_count == 0
    assert context.pages == []


def test_polluted_metadata_refresh_skips_ineligible_rows_without_starving_next_target(
    tmp_path: Path,
) -> None:
    """舊 ineligible 污染 target 不應擋住後續可執行 metadata repair。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        blocked = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="blocked",
                canonical_url="https://www.facebook.com/groups/blocked",
            )
        )
        app.repositories.targets.save(
            replace(
                blocked,
                name="Facebook | Error",
                group_name="Facebook | Error",
            )
        )
        app.services.targets.mark_target_error(blocked.id, "terminal error")
        eligible = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="eligible",
                canonical_url="https://www.facebook.com/groups/eligible",
            )
        )
        app.repositories.targets.save(
            replace(
                eligible,
                name="Facebook | Error",
                group_name="Facebook | Error",
            )
        )

    context = FakeMetadataBrowserContext()
    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=context,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        blocked_after = app.repositories.targets.get(blocked.id)
        eligible_after = app.repositories.targets.get(eligible.id)
    assert refreshed_count == 1
    assert len(context.pages) == 1
    assert blocked_after is not None
    assert blocked_after.name == "Facebook | Error"
    assert eligible_after is not None
    assert eligible_after.name == "測試社團"
    assert eligible_after.group_name == "測試社團"


def test_polluted_metadata_refresh_failure_does_not_block_due_scan(
    tmp_path: Path,
) -> None:
    """metadata repair 失敗只標 metadata failed，不改 scan failure 三次停用語義。"""

    db_path = tmp_path / "app.db"
    scan_calls = 0
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.repositories.targets.save(
            replace(
                target,
                name="Facebook | Error",
                group_name="Facebook | Error",
                enabled=True,
                paused=False,
            )
        )

    async def scan_page(**kwargs: Any) -> object:
        """metadata failure 不應阻擋正式 due scan。"""

        nonlocal scan_calls
        scan_calls += 1
        return build_success_scan_result_for_test(
            target=kwargs["target"],
            page_url=kwargs["page"].url,
        )

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeUnavailableMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
            await target_queue.join()
        finally:
            await executor.stop()

        assert summary.selected_count == 1
        assert summary.metadata_refresh_count == 0
        assert len(metadata_context.pages) == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.runtime_states.get(target.id)
    assert scan_calls == 1
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.FAILED
    assert "無法使用" in updated.metadata_error
    assert state is not None
    assert state.consecutive_failure_count == 0


def test_cover_only_generic_logo_is_queued_for_image_refresh(
    tmp_path: Path,
) -> None:
    """封面通用圖只走 image-only refresh，不覆蓋 target 名稱。"""

    db_path = tmp_path / "app.db"
    generic_logo = "https://static.facebook.com/images/logos/facebook_2x.png"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="測試社團",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                group_cover_image_url=generic_logo,
            )
        )

    refreshed_count = asyncio.run(
        refresh_pending_target_cover_images(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeMetadataBrowserContext(),
        )
    )

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert refreshed_count == 1
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.IDLE


def test_cover_only_generic_logo_is_cleared_when_no_new_cover_is_found(
    tmp_path: Path,
) -> None:
    """generic logo refresh 即使抓不到新封面，也要清掉既有污染 URL。"""

    db_path = tmp_path / "app.db"
    generic_logo = "https://static.facebook.com/images/logos/facebook_2x.png"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="測試社團",
            )
        )
        app.repositories.targets.save(
            replace(
                target,
                group_cover_image_url=generic_logo,
            )
        )

    refreshed_count = asyncio.run(
        refresh_pending_target_cover_images(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeNoCoverMetadataBrowserContext(),
        )
    )

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert refreshed_count == 1
    assert updated is not None
    assert updated.group_cover_image_url == ""
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.IDLE
    assert state.changed is True


def test_cover_refresh_skips_ineligible_pending_job_without_starving_next_target(
    tmp_path: Path,
) -> None:
    """舊 ineligible pending cover job 不應卡住後續可執行 cover refresh。"""

    db_path = tmp_path / "app.db"
    generic_logo = "https://static.facebook.com/images/logos/facebook_2x.png"
    with SqliteApplicationContext(db_path) as app:
        blocked = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="blocked-cover",
                canonical_url="https://www.facebook.com/groups/blocked-cover",
                group_name="阻擋社團",
            )
        )
        app.repositories.targets.save(
            replace(
                blocked,
                group_cover_image_url=generic_logo,
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            blocked.id,
            reported_url=generic_logo,
            min_interval_seconds=21600,
        )
        app.services.targets.mark_target_error(blocked.id, "terminal error")
        eligible = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="eligible-cover",
                canonical_url="https://www.facebook.com/groups/eligible-cover",
                group_name="可修社團",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            eligible.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    refreshed_count = asyncio.run(
        refresh_pending_target_cover_images(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeMetadataBrowserContext(),
        )
    )

    with SqliteApplicationContext(db_path) as app:
        blocked_state = app.repositories.cover_image_refreshes.get(blocked.id)
        eligible_state = app.repositories.cover_image_refreshes.get(eligible.id)
        blocked_after = app.repositories.targets.get(blocked.id)
        eligible_after = app.repositories.targets.get(eligible.id)
    assert refreshed_count == 1
    assert blocked_state is not None
    assert blocked_state.status == TargetCoverImageRefreshStatus.PENDING
    assert eligible_state is not None
    assert eligible_state.status == TargetCoverImageRefreshStatus.IDLE
    assert blocked_after is not None
    assert blocked_after.group_cover_image_url == generic_logo
    assert eligible_after is not None
    assert eligible_after.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"


def test_metadata_refresh_defers_while_failure_retry_scan_is_pending(
    tmp_path: Path,
) -> None:
    """page retry 等待期間，maintenance metadata refresh 不應搶先執行。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="retrying",
                canonical_url="https://www.facebook.com/groups/retrying",
            )
        )
        app.services.targets.restart_target_monitoring(target.id)
        app.services.targets.mark_target_metadata_refresh_pending(target.id)
        decision = app.services.targets.decide_scan_failure(
            target.id,
            PAGE_LOAD_TIMEOUT_REASON,
            source="playwright",
        )
        app.services.targets.apply_scan_failure_decision(
            target.id,
            decision,
            "page load timeout",
        )

    context = FakeMetadataBrowserContext()
    refreshed_count = asyncio.run(
        refresh_requested_target_metadata(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=context,
        )
    )

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.runtime_states.get(target.id)

    assert refreshed_count == 0
    assert context.pages == []
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.PENDING
    assert state is not None
    assert state.runtime_status == TargetRuntimeStatus.IDLE
    assert state.scan_requested_at is not None
    assert state.consecutive_failure_reason == PAGE_LOAD_TIMEOUT_REASON
    assert state.consecutive_failure_count == 1


def test_resident_scheduler_tick_dispatches_existing_pending_outbox(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """resident tick 會 drain 已存在 pending outbox，補上 after-commit hook 漏跑情境。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:item-1:ntfy",
                target_id=target.id,
                item_key="item-1",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.NTFY,
                title="title",
                message="message",
            )
        )
    dispatched_db_paths: list[Path] = []

    def fake_dispatch(**kwargs: object) -> PendingNotificationOutboxDispatchResult:
        db_path_arg = kwargs["db_path"]
        assert isinstance(db_path_arg, Path)
        dispatched_db_paths.append(db_path_arg)
        return PendingNotificationOutboxDispatchResult(
            dispatched_count=1,
            claimed_count=1,
            batch_count=1,
        )

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        fake_dispatch,
    )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("pending outbox dispatch should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=None,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.notification_dispatch_count == 1

    asyncio.run(run_test())

    assert dispatched_db_paths == [db_path]


def test_dispatch_pending_notification_outbox_wakes_registered_dispatcher(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Web UI 主路徑已有 dispatcher 時，resident tick 不做同步外部 dispatch。"""

    db_path = tmp_path / "app.db"
    wake_dispatches: list[Path] = []

    def fake_background_dispatch(**kwargs: object) -> PendingNotificationOutboxDispatchResult:
        db_path_arg = kwargs["db_path"]
        assert isinstance(db_path_arg, Path)
        wake_dispatches.append(db_path_arg)
        return PendingNotificationOutboxDispatchResult(
            dispatched_count=0,
            claimed_count=0,
            batch_count=0,
        )

    def unexpected_sync_dispatch(**_kwargs: object) -> PendingNotificationOutboxDispatchResult:
        raise AssertionError("registered dispatcher should handle pending outbox")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        unexpected_sync_dispatch,
    )
    dispatcher = NotificationOutboxDispatcher(
        db_path=db_path,
        dispatch_pending=fake_background_dispatch,
        stop_timeout_seconds=1,
    )
    dispatcher.start(wake_on_start=False)
    register_notification_outbox_dispatcher(db_path, dispatcher)
    try:
        dispatched_count = dispatch_pending_notification_outbox(
            ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            )
        )
    finally:
        unregister_notification_outbox_dispatcher(db_path, dispatcher)
        assert dispatcher.stop(timeout_seconds=1)

    assert dispatched_count == 0


def test_dispatch_pending_notification_outbox_treats_sqlite_lock_as_transient(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: Any,
) -> None:
    """resident tick 遇到暫時性 SQLite lock 時保留 pending outbox 給下輪重試。"""

    db_path = tmp_path / "app.db"

    def raise_locked(**_kwargs: object) -> PendingNotificationOutboxDispatchResult:
        """模擬 dispatch 開頭遇到其他 writer 持鎖。"""

        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        "facebook_monitor.worker.resident_main.dispatch_new_pending_notification_outbox_for_db",
        raise_locked,
    )

    with caplog.at_level(logging.WARNING, logger="facebook_monitor.worker.resident_main"):
        dispatched_count = dispatch_pending_notification_outbox(
            ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            )
        )

    assert dispatched_count == 0
    assert "database locked" in caplog.text
    assert "Traceback" not in caplog.text


def test_bounded_retention_maintenance_runs_once_per_interval(
    tmp_path: Path,
) -> None:
    """resident bounded retention 每個 DB path 依 interval 節流。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        outbox = app.repositories.notification_outbox.enqueue(
            NotificationOutboxEntry(
                idempotency_key=f"{target.id}:old:desktop",
                target_id=target.id,
                item_key="old",
                item_kind=ItemKind.POST,
                channel=NotificationChannel.DESKTOP,
                title="old",
                message="old",
            )
        ).entry
        assert outbox.id is not None
        claimed = app.repositories.notification_outbox.claim_pending()[0]
        assert app.repositories.notification_outbox.mark_result(
            entry_id=outbox.id,
            status=NotificationOutboxStatus.SENT,
            attempts=1,
            processing_status=claimed.entry.status,
            claim_token=claimed.claim_token,
        )
        app.repositories.notification_outbox.connection.execute(
            """
            UPDATE notification_outbox
            SET updated_at = ?
            WHERE id = ?
            """,
            (encode_datetime(utc_now() - timedelta(days=8)), outbox.id),
        )

    options = ResidentRuntimeOptions(
        db_path=db_path,
        profile_dir=tmp_path / "profile",
    )
    first_deleted = run_bounded_retention_maintenance_if_due(options)
    second_deleted = run_bounded_retention_maintenance_if_due(options)

    with SqliteApplicationContext(db_path) as app:
        remaining_outbox_count = app.repositories.notification_outbox.connection.execute(
            "SELECT COUNT(*) FROM notification_outbox"
        ).fetchone()[0]

    assert first_deleted == 1
    assert second_deleted == 0
    assert remaining_outbox_count == 0


def test_resident_scheduler_tick_refreshes_pending_custom_named_target_cover(
    tmp_path: Path,
) -> None:
    """手動 metadata refresh 不因已有名稱而跳過封面抓取。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id="1370511589953459",
                parent_post_id="2772468963091041",
                canonical_url=(
                    "https://www.facebook.com/groups/1370511589953459/posts/2772468963091041"
                ),
                name="自訂留言 target",
                group_name="既有社團名稱",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("metadata refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 1
        assert summary.metadata_refresh_count == 1
        assert summary.cover_image_refresh_count == 0
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].url == "https://www.facebook.com/groups/1370511589953459"

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.name == "測試社團"
    assert updated.group_name == "測試社團"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""


def test_resident_scheduler_tick_refreshes_cover_image_without_renaming_target(
    tmp_path: Path,
) -> None:
    """自動 cover-only refresh 不覆蓋使用者自訂 target 名稱。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("cover image refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 1
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 1
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].url == "https://www.facebook.com/groups/222518561920110"

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_name == "既有社團名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.IDLE
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == "https://scontent.xx.fbcdn.net/group-cover.jpg"
    assert state.last_result == "succeeded_changed"
    assert state.changed is True


def test_resident_scheduler_tick_skips_stale_cover_image_refresh_job(
    tmp_path: Path,
) -> None:
    """worker 消化前 URL 已被更新時，不應用舊壞圖 job 再開 Facebook。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )
        app.services.target_cover_image_refresh.refresh_target_cover_image_url(
            target.id,
            "https://scontent.xx.fbcdn.net/manual-new.jpg",
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("stale cover image refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 0
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 0
        assert metadata_context.pages == []

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/manual-new.jpg"
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.IDLE
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == "https://scontent.xx.fbcdn.net/manual-new.jpg"
    assert state.last_result == "stale_skipped"
    assert state.changed is False


def test_cover_image_refresh_stale_worker_does_not_clear_newer_request(
    tmp_path: Path,
) -> None:
    """舊 worker 只能完成自己讀到的 cover refresh request，不可清掉較新的 pending row。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )
        stale_worker_state = app.repositories.cover_image_refreshes.get(target.id)
        app.services.target_cover_image_refresh.refresh_target_cover_image_url(
            target.id,
            "https://scontent.xx.fbcdn.net/new-current.jpg",
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/new-current.jpg",
            min_interval_seconds=21600,
        )
    assert stale_worker_state is not None

    async def run_test() -> bool:
        """以舊 state 跑一次 worker refresh，模擬途中又收到新壞圖 request。"""

        return await refresh_target_group_cover_image_from_context(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            browser_context=FakeMetadataBrowserContext(),
            state=stale_worker_state,
        )

    refreshed = asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert refreshed is False
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/new-current.jpg"
    assert state.last_result == "queued"
    assert state.last_resolved_url == ""


def test_resident_scheduler_tick_records_cover_image_refresh_failure(
    tmp_path: Path,
) -> None:
    """cover-only refresh 失敗要留在獨立狀態，不污染 target metadata 狀態。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("cover image refresh failure should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeLoggedOutMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 0
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 0
        assert len(metadata_context.pages) == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert updated is not None
    assert updated.name == "我的自訂名稱"
    assert updated.group_cover_image_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert updated.metadata_status == TargetMetadataStatus.RESOLVED
    assert updated.metadata_error == ""
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.FAILED
    assert state.last_reported_url == "https://scontent.xx.fbcdn.net/old.jpg"
    assert state.last_resolved_url == ""
    assert state.last_result == "failed"
    assert state.changed is False
    assert "Facebook 尚未登入" in state.error


def test_resident_scheduler_tick_keeps_cover_refresh_pending_on_shutdown(
    tmp_path: Path,
    caplog: Any,
) -> None:
    """scheduler 停止造成的 Playwright 關閉不應被記成 cover refresh 失敗。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                name="我的自訂名稱",
                group_name="既有社團名稱",
                group_cover_image_url="https://scontent.xx.fbcdn.net/old.jpg",
            )
        )
        app.services.target_cover_image_refresh.request_refresh_for_current_url(
            target.id,
            reported_url="https://scontent.xx.fbcdn.net/old.jpg",
            min_interval_seconds=21600,
        )

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("shutdown should stop before enqueueing scans")

    async def run_test() -> None:
        stop_requested = False

        def request_stop() -> None:
            nonlocal stop_requested
            stop_requested = True

        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            shutdown_context = FakeShutdownMetadataBrowserContext(request_stop)
            with caplog.at_level(
                logging.INFO,
                logger="facebook_monitor.worker.resident_maintenance_errors",
            ):
                summary = await run_resident_main_scheduler_tick(
                    options=executor.options,
                    browser_context=shutdown_context,
                    page_pool=page_pool,
                    target_queue=target_queue,
                    executor=executor,
                    schedule_planner=planner,
                    cycle_index=1,
                    should_stop=lambda: stop_requested,
                )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.metadata_refresh_count == 0
        assert summary.cover_image_refresh_count == 0
        assert shutdown_context.new_page_count == 1

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        state = app.repositories.cover_image_refreshes.get(target.id)
    assert state is not None
    assert state.status == TargetCoverImageRefreshStatus.PENDING
    assert state.last_result == TargetCoverImageRefreshResult.ATTEMPTED
    assert state.error == ""
    assert "cover image refresh skipped because scheduler is stopping" in caplog.text
    assert "cover image refresh failed" not in caplog.text


def test_resident_scheduler_tick_marks_pending_metadata_failed(tmp_path: Path) -> None:
    """resident metadata refresh 失敗會寫回 failed，避免 UI 永久顯示等待。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app:
        target = app.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
            )
        )
        app.services.targets.mark_target_metadata_refresh_pending(target.id)

    async def scan_page(**kwargs: Any) -> PostsScanSummary:
        """本測試不應執行掃描。"""

        raise AssertionError("metadata refresh should not enqueue scans")

    async def run_test() -> None:
        target_queue = TargetQueue()
        planner = TargetSchedulePlanner()
        page_pool = AsyncResidentPagePool(FakeAsyncBrowserContext())
        executor = ExecutorWorkerPool(
            options=ResidentRuntimeOptions(
                db_path=db_path,
                profile_dir=tmp_path / "profile",
            ),
            page_pool=page_pool,
            target_queue=target_queue,
            schedule_planner=planner,
            scan_page=as_async_scan_callable(scan_page),
        )
        await executor.start()
        try:
            metadata_context = FakeLoggedOutMetadataBrowserContext()
            summary = await run_resident_main_scheduler_tick(
                options=executor.options,
                browser_context=metadata_context,
                page_pool=page_pool,
                target_queue=target_queue,
                executor=executor,
                schedule_planner=planner,
                cycle_index=1,
            )
        finally:
            await executor.stop()

        assert summary.selected_count == 0
        assert summary.closed_page_count == 0
        assert len(metadata_context.pages) == 1
        assert metadata_context.pages[0].closed

    asyncio.run(run_test())

    with SqliteApplicationContext(db_path) as app:
        updated = app.repositories.targets.get(target.id)
    assert updated is not None
    assert updated.metadata_status == TargetMetadataStatus.FAILED
    assert "Facebook 尚未登入" in updated.metadata_error
