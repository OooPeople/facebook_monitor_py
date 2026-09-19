"""One-shot target scan dispatch layer。

職責：提供 fallback/debug one-shot 掃描入口，負責 target 選取、
Playwright context 生命週期與失敗紀錄。此模組不是 scheduler、不是
queue executor，也不是 resident page pool owner；正式產品主路徑為
`worker.resident_main`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.browser_runtime import BrowserRuntimeOptions
from facebook_monitor.automation.browser_runtime import launch_persistent_context_sync
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.defaults import PYTHON_SCHEDULER_RUNTIME_DEFAULTS
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.models import WorkerMode
from facebook_monitor.core.scan_failures import PROFILE_LOCKED_REASON
from facebook_monitor.core.scan_failures import PROFILE_MISSING_REASON
from facebook_monitor.core.scan_failures import SCAN_TIMEOUT_REASON
from facebook_monitor.core.scan_failures import TARGET_ARGUMENT_CONFLICT_REASON
from facebook_monitor.core.scan_failures import TARGET_INVALID_REASON
from facebook_monitor.core.scan_failures import TARGET_MISSING_REASON
from facebook_monitor.core.scan_failure_policy import ScanFailureSource
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.facebook_page_lifecycle import (
    close_existing_context_pages_sync,
)
from facebook_monitor.worker.fallback_automation_admission import (
    FacebookFallbackIncidentRecorded,
)
from facebook_monitor.worker.fallback_automation_admission import (
    FacebookFallbackIncidentPersistenceDeferred,
)
from facebook_monitor.worker.fallback_automation_admission import (
    FacebookFallbackWorkDeferred,
)
from facebook_monitor.worker.fallback_automation_admission import (
    GovernedFallbackPostsWork,
)
from facebook_monitor.worker.fallback_automation_admission import (
    governed_fallback_posts_work,
)
from facebook_monitor.worker.fallback_safety import ensure_target_supported_in_fallback
from facebook_monitor.worker.page_timing import RESIDENT_PAGE_READY_WAIT_MS
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.posts_pipeline import scan_posts_page_sync_and_finalize
from facebook_monitor.worker.scan_commit_guard import ScanCommitGuard
from facebook_monitor.worker.scan_finalize import mark_target_idle_for_scan_commit
from facebook_monitor.worker.scan_failure_finalize import (
    record_guarded_scan_failure_decision_for_db,
)
from facebook_monitor.worker.scan_failure_finalize import record_scan_failure
from facebook_monitor.worker.target_validation import is_valid_posts_target_route
from facebook_monitor.worker.target_validation import validate_posts_target_route


@dataclass(frozen=True)
class OneShotScanOptions:
    """保存 one-shot worker 執行選項。"""

    profile_dir: Path
    db_path: Path
    target_id: str = ""
    group_id: str = ""
    scroll_rounds: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_rounds
    scroll_wait_ms: int = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scroll_wait_ms
    headed_compat: bool = False
    scan_timeout_seconds: float = PYTHON_SCHEDULER_RUNTIME_DEFAULTS.scan_timeout_seconds
    record_failures: bool = True
    scan_worker_id: str = ""
    scan_started_at: datetime | None = None
    scan_page_id: str = ""
    governed_automation_work: GovernedFallbackPostsWork | None = None

    @property
    def commit_guard(self) -> ScanCommitGuard | None:
        """回傳 scheduler admission identity；debug one-shot 直跑時可為空。"""

        if not self.scan_worker_id or self.scan_started_at is None:
            return None
        return ScanCommitGuard(
            worker_id=self.scan_worker_id,
            started_at=self.scan_started_at,
            page_id=self.scan_page_id,
        )


def select_one_shot_target(
    app: ApplicationContext, target_id: str, group_id: str
) -> TargetDescriptor:
    """選取本次 one-shot worker 要掃描的 target。"""

    if target_id and group_id:
        raise WorkerFailure(
            TARGET_ARGUMENT_CONFLICT_REASON,
            "Use either --target-id or --group-id.",
        )

    if target_id:
        target = app.repositories.targets.get(target_id)
        if target is None:
            raise WorkerFailure(TARGET_MISSING_REASON, f"Target not found: {target_id}")
        ensure_target_supported_in_fallback(target, fallback_mode="one_shot")
        validate_posts_target_route(target)
        return target

    if group_id:
        target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, group_id)
        if target is None:
            raise WorkerFailure(
                TARGET_MISSING_REASON,
                f"Group target not found: {group_id}",
            )
        validate_posts_target_route(target)
        return target

    targets = [
        target
        for target in app.repositories.targets.list_enabled()
        if target.target_kind == TargetKind.POSTS
    ]
    invalid_target_ids: list[str] = []
    for target in targets:
        if is_valid_posts_target_route(target):
            return target
        invalid_target_ids.append(target.id)

    if invalid_target_ids:
        raise WorkerFailure(
            TARGET_INVALID_REASON,
            "No valid group posts target found. Invalid target ids: "
            + ", ".join(invalid_target_ids),
        )
    raise WorkerFailure(
        TARGET_MISSING_REASON,
        "No enabled group posts target found in database.",
    )


def record_failure(
    db_path: Path,
    target: TargetDescriptor | None,
    reason: str,
    message: str,
    *,
    exception_class: str = "",
    profile_lease_state: str = "",
    source: ScanFailureSource = "worker_failure",
    commit_guard: ScanCommitGuard | None = None,
    failure_diagnostics: WorkerFailureDiagnostics | None = None,
) -> None:
    """在已知 target 時記錄失敗 scan run。"""

    if target is None:
        return
    worker_path = (
        "one_shot_fallback" if target.target_kind == TargetKind.COMMENTS else "one_shot_posts_scan"
    )
    recorded = record_guarded_scan_failure_decision_for_db(
        db_path=db_path,
        target_id=target.id,
        reason=reason,
        message=message,
        source=source,
        worker_path=worker_path,
        commit_guard=commit_guard,
        exception_class=exception_class,
        profile_lease_state=profile_lease_state,
        failure_diagnostics=failure_diagnostics,
    )
    if recorded is not None:
        return
    if commit_guard is not None:
        return
    with SqliteApplicationContext(db_path) as app:
        record_scan_failure(
            app=app,
            target=target,
            reason=reason,
            message=message,
            worker_path=worker_path,
            exception_class=exception_class,
            profile_lease_state=profile_lease_state,
            failure_diagnostics=failure_diagnostics,
        )


def mark_direct_success_idle(
    *,
    app: ApplicationContext,
    target_id: str,
    summary: PostsScanSummary,
    commit_guard: ScanCommitGuard | None,
) -> None:
    """debug one-shot 無 owner guard 成功時，也要清掉 runtime retry streak。"""

    if commit_guard is not None or summary.scan_skipped:
        return
    mark_target_idle_for_scan_commit(
        app=app,
        target_id=target_id,
        commit_guard=None,
    )


def run_one_shot_scan(options: OneShotScanOptions) -> PostsScanSummary:
    """執行一次已保存 target 掃描，成功時回傳掃描摘要。"""

    target: TargetDescriptor | None = None
    started_at = monotonic()
    effective_scan_timeout_seconds = max(
        options.scan_timeout_seconds,
        PYTHON_SCHEDULER_RUNTIME_DEFAULTS.min_browser_scan_timeout_seconds,
    )
    scan_timeout_ms = effective_scan_timeout_seconds * 1000

    def remaining_timeout_ms() -> float:
        """計算本輪 worker 尚可使用的 Playwright timeout。"""

        elapsed_ms = (monotonic() - started_at) * 1000
        remaining_ms = scan_timeout_ms - elapsed_ms
        if remaining_ms <= 0:
            raise WorkerFailure(
                SCAN_TIMEOUT_REASON,
                f"Worker scan exceeded {int(effective_scan_timeout_seconds)} seconds.",
            )
        return max(remaining_ms, 1000)

    try:
        if options.target_id:
            with SqliteApplicationContext(options.db_path) as app:
                candidate = app.repositories.targets.get(options.target_id)
            if candidate is not None and candidate.target_kind == TargetKind.COMMENTS:
                target = candidate
                ensure_target_supported_in_fallback(target, fallback_mode="one_shot")
        if not options.profile_dir.exists():
            raise WorkerFailure(PROFILE_MISSING_REASON, str(options.profile_dir))
        with SqliteApplicationContext(options.db_path) as app:
            target = select_one_shot_target(app, options.target_id, options.group_id)
            config = app.services.targets.get_config_for_target(target)
        if options.governed_automation_work is not None:
            return _run_governed_one_shot_posts_target(
                options=options,
                target=target,
                config=config,
                work=options.governed_automation_work,
                remaining_timeout_ms=remaining_timeout_ms,
            )
        with governed_fallback_posts_work(
            db_path=options.db_path,
            profile_dir=options.profile_dir,
            profile_lease_factory=acquire_profile_lease,
            profile_lease_owner="one-shot worker",
            owner_alias="one-shot-scan",
        ) as work:
            return _run_governed_one_shot_posts_target(
                options=options,
                target=target,
                config=config,
                work=work,
                remaining_timeout_ms=remaining_timeout_ms,
            )
    except (
        FacebookFallbackIncidentPersistenceDeferred,
        FacebookFallbackIncidentRecorded,
        FacebookFallbackWorkDeferred,
    ):
        # 正常 safety defer 不得寫成 target scan failure。
        raise
    except ProfileLeaseError as error:
        if options.record_failures:
            record_failure(
                options.db_path,
                target,
                PROFILE_LOCKED_REASON,
                str(error),
                exception_class=error.__class__.__name__,
                profile_lease_state="locked",
                commit_guard=options.commit_guard,
            )
        raise WorkerFailure(PROFILE_LOCKED_REASON, str(error)) from error
    except WorkerFailure as error:
        if options.record_failures:
            record_failure(
                options.db_path,
                target,
                error.reason,
                str(error),
                exception_class=error.__class__.__name__,
                commit_guard=options.commit_guard,
                failure_diagnostics=error.diagnostics,
            )
        raise
    except (PlaywrightTimeoutError, PlaywrightError) as error:
        reason = classify_playwright_exception(error)
        if options.record_failures:
            record_failure(
                options.db_path,
                target,
                reason,
                str(error),
                exception_class=error.__class__.__name__,
                source="playwright",
                commit_guard=options.commit_guard,
            )
        raise WorkerFailure(reason, str(error)) from error


def _scan_selected_one_shot_posts_target(
    *,
    options: OneShotScanOptions,
    target: TargetDescriptor,
    config: TargetConfig,
    work: GovernedFallbackPostsWork,
    finish_runtime_owner: bool,
    remaining_timeout_ms: Callable[[], float],
) -> PostsScanSummary:
    """在 caller 已持有 governed profile work 時執行 one-shot browser lifetime。"""

    with sync_playwright() as playwright:
        context = launch_persistent_context_sync(
            playwright,
            BrowserRuntimeOptions(
                profile_dir=options.profile_dir,
                headless=not options.headed_compat,
                timeout_seconds=remaining_timeout_ms() / 1000,
            ),
        )
        try:
            context.set_default_timeout(remaining_timeout_ms())
            context.set_default_navigation_timeout(remaining_timeout_ms())
            close_existing_context_pages_sync(context)
            page = context.new_page()
            page.goto(
                target.canonical_url,
                wait_until="domcontentloaded",
                timeout=remaining_timeout_ms(),
            )
            context.set_default_timeout(remaining_timeout_ms())
            page.wait_for_timeout(RESIDENT_PAGE_READY_WAIT_MS)
            context.set_default_timeout(remaining_timeout_ms())
            with work.application_context() as app:
                summary = scan_posts_page_sync_and_finalize(
                    page=page,
                    app=app,
                    target=target,
                    config=config,
                    scroll_rounds=options.scroll_rounds,
                    scroll_wait_ms=options.scroll_wait_ms,
                    commit_guard=options.commit_guard,
                )
                if finish_runtime_owner:
                    mark_target_idle_for_scan_commit(
                        app=app,
                        target_id=target.id,
                        commit_guard=options.commit_guard,
                    )
                else:
                    mark_direct_success_idle(
                        app=app,
                        target_id=target.id,
                        summary=summary,
                        commit_guard=options.commit_guard,
                    )
            return summary
        finally:
            context.close()
            work.note_browser_context_closed()


def _run_governed_one_shot_posts_target(
    *,
    options: OneShotScanOptions,
    target: TargetDescriptor,
    config: TargetConfig,
    work: GovernedFallbackPostsWork,
    remaining_timeout_ms: Callable[[], float],
) -> PostsScanSummary:
    """確保 one-shot 有 scan owner，並將 temporary block 導入 profile incident。"""

    effective_options = options
    direct_claimed = effective_options.commit_guard is None
    if direct_claimed:
        worker_id = f"one-shot-direct-{uuid4()}"
        with work.application_context() as app:
            locked_state = app.services.targets.try_claim_target_running(
                target.id,
                worker_id,
            )
        if locked_state is None:
            raise WorkerFailure(
                TARGET_INVALID_REASON,
                "One-shot target could not acquire an active scan owner.",
            )
        effective_options = replace(
            options,
            scan_worker_id=worker_id,
            scan_started_at=locked_state.last_started_at,
            scan_page_id=locked_state.active_page_id,
            governed_automation_work=work,
        )
    commit_guard = effective_options.commit_guard
    if commit_guard is None:
        raise RuntimeError("governed one-shot scan owner is missing")
    try:
        return _scan_selected_one_shot_posts_target(
            options=effective_options,
            target=target,
            config=config,
            work=work,
            finish_runtime_owner=direct_claimed,
            remaining_timeout_ms=remaining_timeout_ms,
        )
    except WorkerFailure as error:
        if work.record_temporary_block_incident(
            error=error,
            target_id=target.id,
            commit_guard=commit_guard,
            worker_mode=(
                WorkerMode.HEADED_COMPAT if options.headed_compat else WorkerMode.HEADLESS
            ),
        ):
            raise FacebookFallbackIncidentRecorded(error.reason, str(error)) from error
        raise
