"""Facebook temporary-block advisory warning application service。"""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import re

from facebook_monitor.core.defaults import FacebookTemporaryBlockWarningDefaults
from facebook_monitor.core.defaults import (
    PYTHON_FACEBOOK_TEMPORARY_BLOCK_WARNING_DEFAULTS,
)
from facebook_monitor.core.facebook_temporary_block import (
    FACEBOOK_TEMPORARY_BLOCK_FORMAL_SOURCE_KINDS,
)
from facebook_monitor.core.facebook_temporary_block import FacebookAccessSignalConfidence
from facebook_monitor.core.facebook_temporary_block import TemporaryBlockFinding
from facebook_monitor.core.facebook_temporary_block import (
    TemporaryBlockWarningSnapshot,
)
from facebook_monitor.core.models import utc_now
from facebook_monitor.core.scan_failures import FACEBOOK_TEMPORARY_BLOCK_REASON
from facebook_monitor.persistence.repositories.facebook_temporary_block_warning import (
    FacebookTemporaryBlockWarningRepository,
)


_SAFE_EVIDENCE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class FacebookTemporaryBlockWarningService:
    """驗證 finding 並保存固定十二小時的 singleton warning。"""

    def __init__(
        self,
        repository: FacebookTemporaryBlockWarningRepository,
        *,
        defaults: FacebookTemporaryBlockWarningDefaults = (
            PYTHON_FACEBOOK_TEMPORARY_BLOCK_WARNING_DEFAULTS
        ),
    ) -> None:
        self.repository = repository
        self.defaults = defaults

    def get(self) -> TemporaryBlockWarningSnapshot | None:
        """讀取目前 warning；不把期限解讀為 admission gate。"""

        return self.repository.get()

    def record(
        self,
        finding: TemporaryBlockFinding,
        *,
        detected_at: datetime | None = None,
    ) -> TemporaryBlockWarningSnapshot:
        """驗證 confirmed finding，推進 generation 並重設十二小時期限。"""

        self.validate(finding)
        observed_at = _require_utc(detected_at or utc_now())
        warning_seconds = self.defaults.warning_seconds
        if warning_seconds <= 0:
            raise ValueError("temporary block warning seconds must be positive")
        return self.repository.record(
            detected_at=observed_at,
            warning_until=observed_at + timedelta(seconds=warning_seconds),
            source_kind=finding.source_kind,
            operation_kind=finding.operation_kind,
            action_kind=finding.action_kind,
            updated_at=observed_at,
        )

    def validate(self, finding: TemporaryBlockFinding) -> None:
        """驗證 finding 只含正式來源與 bounded high-confidence evidence。"""

        if (
            finding.reason_code != FACEBOOK_TEMPORARY_BLOCK_REASON
            or finding.confidence != FacebookAccessSignalConfidence.HIGH
            or finding.source_kind not in FACEBOOK_TEMPORARY_BLOCK_FORMAL_SOURCE_KINDS
            or not _SAFE_EVIDENCE_CODE.fullmatch(finding.evidence_code)
        ):
            raise ValueError("invalid temporary block finding")


def _require_utc(value: datetime) -> datetime:
    """要求 timestamp 為 UTC aware，避免 DB lexical ordering 漂移。"""

    if value.utcoffset() != timedelta(0):
        raise ValueError("temporary block warning datetime must be UTC")
    return value


__all__ = ["FacebookTemporaryBlockWarningService"]
