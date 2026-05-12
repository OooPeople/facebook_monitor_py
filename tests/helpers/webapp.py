"""Web app test helpers。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.services import RecordScanRequest
from facebook_monitor.application.services import UpsertGroupPostsTargetRequest
from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import LatestScanItem
from facebook_monitor.core.models import NotificationChannel
from facebook_monitor.core.models import NotificationEvent
from facebook_monitor.core.models import NotificationStatus
from facebook_monitor.core.models import ScanStatus
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.webapp.app import create_app
from facebook_monitor.webapp.profile_session import ProfileSessionOptions
from facebook_monitor.webapp.scheduler_session import SchedulerSessionOptions
from facebook_monitor.webapp.scheduler_session import SchedulerSessionState


class FakeProfileManager:
    """測試用 profile manager，避免 Web UI route 測試真的開 Playwright。"""

    def __init__(self) -> None:
        self.active = False
        self.options: ProfileSessionOptions | None = None

    def is_active(self) -> bool:
        """回傳 fake profile 視窗是否開啟。"""

        return self.active

    def open(self, options: ProfileSessionOptions) -> None:
        """保存設定並標記 fake profile 視窗已開啟。"""

        self.options = options
        self.active = True

    def close(self) -> None:
        """關閉 fake profile 視窗。"""

        self.active = False


class FakeSchedulerManager:
    """測試用 scheduler manager，避免 Web UI route 測試真的跑背景掃描。"""

    def __init__(self) -> None:
        self.running = False
        self.started_count = 0
        self.stopped_count = 0
        self.woken_count = 0
        self.options: SchedulerSessionOptions | None = None
        self.queued_target_ids: tuple[str, ...] = ()
        self.metadata_refresh_target_ids: list[str] = []

    def state(self) -> SchedulerSessionState:
        """回傳 fake scheduler 狀態。"""

        return SchedulerSessionState(
            running=self.running,
            interval_seconds=60,
            last_cycle_at="",
            last_error="",
            max_concurrent_scans=2,
            current_running_count=1 if self.running else 0,
            current_queued_count=len(self.queued_target_ids),
            queue_length=len(self.queued_target_ids),
            queued_target_ids=self.queued_target_ids,
            worker_ids=("resident-slot-1", "resident-slot-2") if self.running else (),
            page_pool_size=1 if self.running else 0,
            last_opened_page_count=1 if self.running else 0,
            last_reused_page_count=2 if self.running else 0,
            last_closed_page_count=0,
            resident_browser_alive=self.running,
        )

    def is_running(self) -> bool:
        """回傳 fake scheduler 是否執行中。"""

        return self.running

    def start(self, options: SchedulerSessionOptions) -> None:
        """標記 fake scheduler 已啟動。"""

        self.started_count += 1
        self.options = options
        self.running = True

    def stop(self) -> None:
        """標記 fake scheduler 已停止。"""

        self.stopped_count += 1
        self.running = False

    def wake(self) -> None:
        """記錄 manual-start 喚醒要求。"""

        self.woken_count += 1

    def request_metadata_refresh(self, target_id: str) -> None:
        """記錄 target metadata refresh 要求。"""

        self.metadata_refresh_target_ids.append(target_id)


def seed_dashboard_index_target(db_path: Path) -> TargetDescriptor:
    """建立首頁 target card 測試共用資料。"""

    with SqliteApplicationContext(db_path) as app_context:
        target = app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222518561920110",
                canonical_url="https://www.facebook.com/groups/222518561920110",
                group_name="(3) 測試社團",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.FAILED,
                error_message="page_load_timeout: timeout",
            )
        )
        app_context.services.scans.record_scan(
            RecordScanRequest(
                target_id=target.id,
                status=ScanStatus.SUCCESS,
                item_count=2,
                matched_count=1,
                metadata={
                    "worker": "posts_scan",
                    "collection_strategy": "feed_visible_window",
                    "new_count": 1,
                    "matched_count": 1,
                    "target_count": 5,
                    "scanned_count": 2,
                    "candidate_count": 2,
                    "round_count": 1,
                    "scroll_rounds": 0,
                    "scroll_wait_ms": 0,
                    "stop_reason": "scroll_rounds_completed",
                    "rounds": [
                        {
                            "round_index": 0,
                            "raw_item_count": 2,
                            "unique_item_count": 2,
                            "scroll_y": 0,
                            "scroll_height": 1200,
                        }
                    ],
                },
            )
        )
        app_context.repositories.latest_scan_items.replace_for_target(
            target.id,
            [
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="item-1",
                    item_index=0,
                    author="王小明",
                    text="這是一篇有票券關鍵字的貼文",
                    permalink="https://www.facebook.com/groups/222518561920110/posts/1",
                    matched_keyword="票券",
                    debug_metadata={
                        "textSource": "primary",
                        "permalinkSource": "container:groups_post_anchor",
                        "expandCount": 1,
                        "linkDiagnostics": {
                            "total": 2,
                            "kindCounts": {"profile": 1, "hashtag": 1},
                            "samples": [
                                {
                                    "kind": "profile",
                                    "href": "https://www.facebook.com/groups/1/user/2",
                                    "text": "王小明",
                                }
                            ],
                        },
                    },
                ),
                LatestScanItem(
                    target_id=target.id,
                    scan_run_id=1,
                    item_kind=ItemKind.POST,
                    item_key="item-2",
                    item_index=1,
                    author="陳小華",
                    text="這是一篇普通貼文",
                    permalink="",
                    matched_keyword="",
                ),
            ],
        )
        app_context.repositories.notification_events.add(
            NotificationEvent(
                target_id=target.id,
                item_key="item-1",
                channel=NotificationChannel.NTFY,
                status=NotificationStatus.SENT,
                message="sent",
            )
        )
    return target


def render_seeded_index(tmp_path: Path, *, scheduler_running: bool = True) -> tuple[str, str]:
    """回傳已建立 target 的首頁 HTML 與 target id。"""

    db_path = tmp_path / "app.db"
    target = seed_dashboard_index_target(db_path)

    scheduler_manager = FakeSchedulerManager()
    scheduler_manager.running = scheduler_running
    client = TestClient(
        create_app(
            db_path=db_path,
            profile_dir=tmp_path / "profile",
            scheduler_manager=scheduler_manager,
            enforce_csrf=False,
        )
    )
    response = client.get("/")

    assert response.status_code == 200
    return response.text, target.id
