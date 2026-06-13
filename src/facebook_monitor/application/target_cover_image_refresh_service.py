"""Target cover image refresh application service。

職責：集中 image-only cover refresh request、worker state 寫回與封面 URL
更新，避免這條 maintenance flow 混在一般 target registry facade 裡。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from facebook_monitor.application.target_registry_service import TargetRegistryService
from facebook_monitor.core.models import CoverImageRefreshRequestStatus
from facebook_monitor.core.models import TargetCoverImageRefreshResult
from facebook_monitor.core.models import TargetCoverImageRefreshState
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.persistence.repositories.target_cover_image_refresh import (
    TargetCoverImageRefreshRepository,
)
from facebook_monitor.persistence.repositories.targets import TargetRepository


@dataclass(frozen=True)
class CoverImageRefreshRequestResult:
    """描述 UI 壞圖上報轉成背景刷新排程的結果。"""

    status: CoverImageRefreshRequestStatus
    queued: bool = False
    reason: str = ""


class TargetCoverImageRefreshService:
    """管理 target cover image-only refresh 的 application service。"""

    def __init__(
        self,
        *,
        targets: TargetRepository,
        cover_image_refreshes: TargetCoverImageRefreshRepository,
        registry: TargetRegistryService,
    ) -> None:
        self.targets = targets
        self.cover_image_refreshes = cover_image_refreshes
        self.registry = registry

    def request_refresh_for_current_url(
        self,
        target_id: str,
        *,
        reported_url: str,
        min_interval_seconds: int,
    ) -> CoverImageRefreshRequestResult:
        """依 UI 壞圖 hint 排程 image-only cover refresh。"""

        target = self.targets.get(target_id)
        if target is None:
            return CoverImageRefreshRequestResult(
                status=CoverImageRefreshRequestStatus.NOT_FOUND,
                reason="target_not_found",
            )
        normalized_reported_url = reported_url.strip()
        if not normalized_reported_url:
            return CoverImageRefreshRequestResult(
                status=CoverImageRefreshRequestStatus.INVALID_URL,
                reason="missing_reported_url",
            )
        if normalized_reported_url != target.group_cover_image_url.strip():
            return CoverImageRefreshRequestResult(
                status=CoverImageRefreshRequestStatus.IGNORED_STALE_URL,
                reason="reported_url_is_not_current",
            )
        status = self.cover_image_refreshes.request_refresh(
            target_id=target.id,
            reported_url=normalized_reported_url,
            min_interval_seconds=min_interval_seconds,
        )
        return CoverImageRefreshRequestResult(
            status=status,
            queued=status == CoverImageRefreshRequestStatus.QUEUED,
        )

    def list_pending(
        self,
        *,
        limit: int,
        exclude_target_ids: tuple[str, ...] = (),
    ) -> list[TargetCoverImageRefreshState]:
        """列出等待 resident worker 消化的 image-only cover refresh jobs。"""

        return self.cover_image_refreshes.list_pending(
            limit=limit,
            exclude_target_ids=exclude_target_ids,
        )

    def mark_attempted(
        self,
        target_id: str,
        *,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """記錄 target cover image refresh 已開始嘗試。"""

        return self.cover_image_refreshes.mark_attempted(
            target_id,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_succeeded(
        self,
        target_id: str,
        *,
        resolved_url: str,
        changed: bool,
        result: TargetCoverImageRefreshResult | None = None,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """標記 target cover image refresh 成功。"""

        return self.cover_image_refreshes.mark_succeeded(
            target_id,
            resolved_url=resolved_url,
            changed=changed,
            result=result,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_stale_skipped(
        self,
        target_id: str,
        *,
        current_url: str,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """現行圖片 URL 已非 UI 上報 URL 時，清除過期 cover refresh job。"""

        return self.cover_image_refreshes.mark_stale_skipped(
            target_id,
            current_url=current_url,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def mark_failed(
        self,
        target_id: str,
        error: str,
        *,
        result: TargetCoverImageRefreshResult = TargetCoverImageRefreshResult.FAILED,
        reported_url: str | None = None,
        requested_at: datetime | None = None,
    ) -> bool:
        """標記 target cover image refresh 失敗。"""

        return self.cover_image_refreshes.mark_failed(
            target_id,
            error,
            result=result,
            reported_url=reported_url,
            requested_at=requested_at,
        )

    def refresh_target_cover_image_url(
        self,
        target_id: str,
        group_cover_image_url: str,
    ) -> TargetDescriptor:
        """只更新 target 社團封面圖 URL，不覆蓋名稱或 metadata status。"""

        return self.registry.refresh_target_group_cover_image(
            target_id,
            group_cover_image_url,
        )


__all__ = [
    "CoverImageRefreshRequestResult",
    "TargetCoverImageRefreshService",
]
