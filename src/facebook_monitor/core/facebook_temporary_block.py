"""Facebook temporary-block 最小 durable warning domain。

職責：定義高可信 finding 與 singleton warning snapshot，不承載 admission、
recovery、profile identity 或 browser runtime 狀態。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal


class FacebookAccessSignalConfidence(StrEnum):
    """Temporary-block finding 可接受的信心等級。"""

    HIGH = "high"


class FacebookWorkSourceKind(StrEnum):
    """可建立現行 warning 的正式 Facebook work 來源。"""

    SCAN = "scan"
    METADATA = "metadata"
    COVER = "cover"
    SYNC_RESOLVER = "sync_resolver"


class FacebookProductOperationKind(StrEnum):
    """不綁 transport 的 Facebook 產品操作意圖。"""

    POSTS_ACCESS = "posts_access"
    COMMENTS_ACCESS = "comments_access"
    GROUP_METADATA_ACCESS = "group_metadata_access"
    COVER_METADATA_ACCESS = "cover_metadata_access"
    UNKNOWN = "unknown"


class FacebookActionKind(StrEnum):
    """實際觸發 finding 的 browser action。"""

    GROUP_FEED_DOCUMENT = "group_feed_document"
    GROUP_DOCUMENT = "group_document"
    DIRECT_DOCUMENT = "direct_document"
    RELOAD = "reload"
    TRUSTED_CLICK = "trusted_click"
    UNKNOWN = "unknown"


FACEBOOK_TEMPORARY_BLOCK_FORMAL_SOURCE_KINDS = frozenset(
    {
        FacebookWorkSourceKind.SCAN,
        FacebookWorkSourceKind.METADATA,
        FacebookWorkSourceKind.COVER,
        FacebookWorkSourceKind.SYNC_RESOLVER,
    }
)
"""可建立現行 warning 的正式 runtime source 集合。"""


@dataclass(frozen=True)
class TemporaryBlockFinding:
    """保存正式 runtime 產生的 bounded high-confidence block finding。"""

    source_kind: FacebookWorkSourceKind
    operation_kind: FacebookProductOperationKind
    action_kind: FacebookActionKind
    target_id: str | None = None
    evidence_code: str = "facebook_page_guard"
    reason_code: Literal["facebook_temporary_block"] = "facebook_temporary_block"
    confidence: FacebookAccessSignalConfidence = FacebookAccessSignalConfidence.HIGH


@dataclass(frozen=True)
class TemporaryBlockWarningSnapshot:
    """保存最近一次 confirmed temporary block 的 advisory warning。"""

    generation: int
    detected_at: datetime
    warning_until: datetime
    source_kind: FacebookWorkSourceKind
    operation_kind: FacebookProductOperationKind
    action_kind: FacebookActionKind
    updated_at: datetime

    def is_active(self, now: datetime) -> bool:
        """回傳指定時間是否仍在 advisory warning 期限內。"""

        return now < self.warning_until


__all__ = [
    "FACEBOOK_TEMPORARY_BLOCK_FORMAL_SOURCE_KINDS",
    "FacebookAccessSignalConfidence",
    "FacebookActionKind",
    "FacebookProductOperationKind",
    "FacebookWorkSourceKind",
    "TemporaryBlockFinding",
    "TemporaryBlockWarningSnapshot",
]
