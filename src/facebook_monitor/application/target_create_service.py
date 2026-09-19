"""Target create application use case。

職責：集中「Facebook URL -> posts/comments target」的產品流程，讓 Web route
只負責 HTTP/form adapter 與 redirect；Facebook metadata 一律交給 resident 補齊。
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
class CreateTargetPlan:
    """保存 target create 的 route detection 與 deferred metadata 決策。"""

    route: DetectedCommentsTargetRoute | DetectedPostsTargetRoute
    custom_name: str

    @property
    def should_request_metadata_refresh(self) -> bool:
        """回傳 commit 後是否應要求 resident 背景補 metadata。"""

        return not self.custom_name


@dataclass(frozen=True)
class CreateTargetResult:
    """保存 target create use case 的結果與 commit 後副作用指示。"""

    target: TargetDescriptor
    metadata_refresh_target_id: str = ""

    @property
    def metadata_refresh_pending(self) -> bool:
        """回傳名稱與封面是否已交由 resident 稍後補齊。"""

        return bool(self.metadata_refresh_target_id)


def build_create_target_plan(
    *,
    group_url: str,
    display_name: str,
) -> CreateTargetPlan:
    """從使用者輸入建立 target create plan。"""

    return CreateTargetPlan(
        route=detect_target_route_from_url(normalize_target_url(group_url)),
        custom_name=clean_facebook_page_title(normalize_display_name(display_name)),
    )


def create_or_update_target_from_plan(
    targets: TargetApplicationService,
    *,
    plan: CreateTargetPlan,
    config: TargetConfigPatch,
) -> CreateTargetResult:
    """先依 create plan upsert target，缺名稱時再標記 metadata pending。"""

    if isinstance(plan.route, DetectedCommentsTargetRoute):
        target = targets.upsert_comments_target(
            UpsertCommentsTargetRequest(
                group_id=plan.route.group_id,
                parent_post_id=plan.route.parent_post_id,
                canonical_url=plan.route.canonical_url,
                name=plan.custom_name,
                config=config,
            )
        )
    else:
        target = targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id=plan.route.group_id,
                canonical_url=plan.route.canonical_url,
                name=plan.custom_name,
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
    "build_create_target_plan",
    "create_or_update_target_from_plan",
]
