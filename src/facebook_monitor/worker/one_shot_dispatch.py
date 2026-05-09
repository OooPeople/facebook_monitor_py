"""One-shot target scan dispatch layer。

職責：提供 fallback/debug one-shot 掃描入口，負責 target 選取、
Playwright context 生命週期與失敗紀錄。此模組不是 scheduler、不是
queue executor，也不是 resident page pool owner；正式產品主路徑為
`worker.resident_main`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from facebook_monitor.application.context import ApplicationContext
from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.automation.profile_lease import ProfileLeaseError
from facebook_monitor.automation.profile_lease import acquire_profile_lease
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.errors import classify_playwright_exception
from facebook_monitor.worker.posts_pipeline import PostsScanSummary
from facebook_monitor.worker.posts_pipeline import scan_posts_page
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
    scroll_rounds: int = 3
    scroll_wait_ms: int = 2500
    headed_compat: bool = False
    scan_timeout_seconds: float = 120


def select_one_shot_target(app: ApplicationContext, target_id: str, group_id: str) -> TargetDescriptor:
    """選取本次 one-shot worker 要掃描的 target。"""

    if target_id and group_id:
        raise WorkerFailure("target_argument_conflict", "Use either --target-id or --group-id.")

    if target_id:
        target = app.repositories.targets.get(target_id)
        if target is None:
            raise WorkerFailure("target_missing", f"Target not found: {target_id}")
        if target.target_kind != TargetKind.POSTS:
            raise WorkerFailure("target_kind_unsupported", "Only group posts targets are supported.")
        validate_posts_target_route(target)
        return target

    if group_id:
        target = app.repositories.targets.find_by_kind_scope(TargetKind.POSTS, group_id)
        if target is None:
            raise WorkerFailure("target_missing", f"Group target not found: {group_id}")
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
            "target_invalid",
            "No valid group posts target found. Invalid target ids: "
            + ", ".join(invalid_target_ids),
        )
    raise WorkerFailure("target_missing", "No enabled group posts target found in database.")


def record_failure(
    db_path: Path,
    target: TargetDescriptor | None,
    reason: str,
    message: str,
    *,
    exception_class: str = "",
    profile_lease_state: str = "",
) -> None:
    """在已知 target 時記錄失敗 scan run。"""

    if target is None:
        return
    with SqliteApplicationContext(db_path) as app:
        record_scan_failure(
            app=app,
            target=target,
            reason=reason,
            message=message,
            worker_path="one_shot_posts_scan",
            exception_class=exception_class,
            profile_lease_state=profile_lease_state,
        )


def run_one_shot_scan(options: OneShotScanOptions) -> PostsScanSummary:
    """執行一次已保存 target 掃描，成功時回傳掃描摘要。"""

    if not options.profile_dir.exists():
        raise WorkerFailure("profile_missing", str(options.profile_dir))

    target: TargetDescriptor | None = None
    started_at = monotonic()
    effective_scan_timeout_seconds = max(options.scan_timeout_seconds, 10)
    scan_timeout_ms = effective_scan_timeout_seconds * 1000

    def remaining_timeout_ms() -> float:
        """計算本輪 worker 尚可使用的 Playwright timeout。"""

        elapsed_ms = (monotonic() - started_at) * 1000
        remaining_ms = scan_timeout_ms - elapsed_ms
        if remaining_ms <= 0:
            raise WorkerFailure(
                "scan_timeout",
                f"Worker scan exceeded {int(effective_scan_timeout_seconds)} seconds.",
            )
        return max(remaining_ms, 1000)

    with SqliteApplicationContext(options.db_path) as app:
        try:
            target = select_one_shot_target(app, options.target_id, options.group_id)
            config = app.services.targets.get_config_for_target(target)

            with acquire_profile_lease(options.profile_dir, "one-shot worker"):
                with sync_playwright() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=str(options.profile_dir),
                        headless=not options.headed_compat,
                        viewport={"width": 1366, "height": 900},
                        timeout=remaining_timeout_ms(),
                    )
                    try:
                        context.set_default_timeout(remaining_timeout_ms())
                        context.set_default_navigation_timeout(remaining_timeout_ms())
                        page = context.new_page()
                        page.goto(
                            target.canonical_url,
                            wait_until="domcontentloaded",
                            timeout=remaining_timeout_ms(),
                        )
                        context.set_default_timeout(remaining_timeout_ms())
                        page.wait_for_timeout(5000)
                        context.set_default_timeout(remaining_timeout_ms())
                        return scan_posts_page(
                            page=page,
                            app=app,
                            target=target,
                            config=config,
                            scroll_rounds=options.scroll_rounds,
                            scroll_wait_ms=options.scroll_wait_ms,
                        )
                    finally:
                        context.close()
        except ProfileLeaseError as error:
            record_failure(
                options.db_path,
                target,
                "profile_locked",
                str(error),
                exception_class=error.__class__.__name__,
                profile_lease_state="locked",
            )
            raise WorkerFailure("profile_locked", str(error)) from error
        except WorkerFailure as error:
            record_failure(
                options.db_path,
                target,
                error.reason,
                str(error),
                exception_class=error.__class__.__name__,
            )
            raise
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            reason = classify_playwright_exception(error)
            record_failure(
                options.db_path,
                target,
                reason,
                str(error),
                exception_class=error.__class__.__name__,
            )
            raise WorkerFailure(reason, str(error)) from error
