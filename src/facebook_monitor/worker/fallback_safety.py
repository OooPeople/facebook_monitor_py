"""Fallback/debug worker 的安全能力邊界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from typing import Mapping

from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.core.models import TargetKind
from facebook_monitor.core.scan_failures import UNSUPPORTED_IN_FALLBACK_REASON
from facebook_monitor.worker.errors import WorkerFailure
from facebook_monitor.worker.failure_diagnostics import WorkerFailureDiagnostics


FallbackMode = Literal["one_shot", "sync_resident_fallback"]


@dataclass(frozen=True)
class UnsupportedFallbackDiagnostics(WorkerFailureDiagnostics):
    """保存 fallback 在 browser work 前拒絕 comments 的安全診斷。"""

    fallback_mode: FallbackMode
    detector_version: int = 1

    def to_safe_mapping(self) -> Mapping[str, object]:
        """輸出不含 target identity、URL 或頁面內容的固定 mapping。"""

        return {
            "fallback_guard": {
                "detector": "fallback_capability_guard",
                "detector_version": self.detector_version,
                "classification": UNSUPPORTED_IN_FALLBACK_REASON,
                "fallback_mode": self.fallback_mode,
                "target_kind": TargetKind.COMMENTS.value,
                "browser_work_started": False,
            }
        }


def ensure_target_supported_in_fallback(
    target: TargetDescriptor,
    *,
    fallback_mode: FallbackMode,
) -> None:
    """在任何 profile lease/browser work 前拒絕 comments fallback。"""

    if target.target_kind != TargetKind.COMMENTS:
        return
    raise WorkerFailure(
        UNSUPPORTED_IN_FALLBACK_REASON,
        "Comments targets are unsupported in this fallback mode.",
        diagnostics=UnsupportedFallbackDiagnostics(fallback_mode=fallback_mode),
    )


__all__ = [
    "FallbackMode",
    "UnsupportedFallbackDiagnostics",
    "ensure_target_supported_in_fallback",
]
