"""Target create application use case。

職責：集中「Facebook URL -> posts/comments target」的產品流程，讓 Web route
只負責 HTTP/form/profile adapter 與 redirect。
"""

from __future__ import annotations

from dataclasses import dataclass

from facebook_monitor.application.services import TargetApplicationService
from facebook_monitor.application.target_requests import TargetConfigPatch
from facebook_monitor.application.target_requests import UpsertCommentsTargetRequest
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from facebook_monitor.application.target_route_service import DetectedCommentsTargetRoute
from facebook_monitor.application.target_route_service import DetectedPostsTargetRoute
from facebook_monitor.application.target_route_service import detect_target_route_from_url
from facebook_monitor.core.input_limits import normalize_display_name
from facebook_monitor.core.input_limits import normalize_target_url
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.facebook.route_detection import clean_facebook_page_title


@dataclass(frozen=True)
class TargetCreateMetadata:
    """保存新增 target 時已解析的 Facebook metadata。"""

    group_name: str = ""
    group_cover_image_url: str = ""


@dataclass(frozen=True)
class CreateTargetPlan:
    """保存 target create 的 route detection 與 metadata 決策。"""

    route: DetectedCommentsTargetRoute | DetectedPostsTargetRoute
    custom_name: str
    scheduler_running: bool

    @property
    def metadata_canonical_url(self) -> str:
        """回傳 profile resolver 應開啟的 Facebook canonical URL。"""

        if isinstance(self.route, DetectedCommentsTargetRoute):
            return self.route.group_canonical_url
        return self.route.canonical_url

    @property
    def should_resolve_metadata(self) -> bool:
        """回傳 Web adapter 是否應同步解析 group metadata。"""

        return not self.custom_name and not self.scheduler_running

    @property
    def should_request_metadata_refresh(self) -> bool:
        """回傳 commit 後是否應要求 scheduler 背景補 metadata。"""

        return not self.custom_name and self.scheduler_running


@dataclass(frozen=True)
class CreateTargetResult:
    """保存 target create use case 的結果與 commit 後副作用指示。"""

    target: TargetDescriptor
    metadata_refresh_target_id: str = ""


def build_create_target_plan(
    *,
    group_url: str,
    display_name: str,
    scheduler_running: bool,
) -> CreateTargetPlan:
    """從使用者輸入建立 target create plan。"""

    return CreateTargetPlan(
        route=detect_target_route_from_url(normalize_target_url(group_url)),
        custom_name=clean_facebook_page_title(normalize_display_name(display_name)),
        scheduler_running=scheduler_running,
    )


def create_or_update_target_from_plan(
    targets: TargetApplicationService,
    *,
    plan: CreateTargetPlan,
    config: TargetConfigPatch,
    metadata: TargetCreateMetadata = TargetCreateMetadata(),
) -> CreateTargetResult:
    """依 create plan 建立或更新 posts/comments target。"""

    if isinstance(plan.route, DetectedCommentsTargetRoute):
        target = targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id=plan.route.group_id,
                parent_post_id=plan.route.parent_post_id,
                canonical_url=plan.route.canonical_url,
                name=plan.custom_name,
                group_name=metadata.group_name,
                group_cover_image_url=metadata.group_cover_image_url,
                config=config,
            )
        )
    else:
        target = targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=plan.route.group_id,
                canonical_url=plan.route.canonical_url,
                name=plan.custom_name,
                group_name=metadata.group_name,
                group_cover_image_url=metadata.group_cover_image_url,
                config=config,
            )
        )
    if not plan.should_request_metadata_refresh:
        return CreateTargetResult(target=target)
    refreshed_target = targets.mark_target_metadata_refresh_pending(target.id)
    return CreateTargetResult(
        target=refreshed_target,
        metadata_refresh_target_id=refreshed_target.id,
    )


__all__ = [
    "CreateTargetPlan",
    "CreateTargetResult",
    "TargetCreateMetadata",
    "build_create_target_plan",
    "create_or_update_target_from_plan",
]
