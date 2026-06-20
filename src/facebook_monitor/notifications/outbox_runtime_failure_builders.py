"""Runtime failure notification outbox payload builders。"""

from __future__ import annotations

from facebook_monitor.core.models import ItemKind
from facebook_monitor.core.models import NotificationEventKind
from facebook_monitor.core.models import TargetConfig
from facebook_monitor.core.models import TargetDescriptor
from facebook_monitor.notifications.channel_plan import build_enabled_channel_plans
from facebook_monitor.notifications.desktop_format import (
    build_runtime_failure_compact_notification_message,
)
from facebook_monitor.notifications.outbox_entry_builders import (
    NotificationOutboxChannelPayload,
)
from facebook_monitor.notifications.runtime_failure_message_builders import (
    build_runtime_failure_notification_message,
)


def build_runtime_failure_item_key(scan_run_id: int) -> str:
    """建立 runtime failure outbox item key。"""

    return f"runtime-failure:{scan_run_id}"


def build_runtime_failure_item_kind(target: TargetDescriptor) -> ItemKind:
    """依 target 類型建立 runtime failure outbox item kind。"""

    return ItemKind.COMMENT if target.target_kind.value == "comments" else ItemKind.POST


def normalize_runtime_failure_count(failure_count: int) -> int:
    """正規化 runtime failure 連續次數，至少為 1。"""

    return max(int(failure_count), 1)


def build_runtime_failure_channel_payloads(
    *,
    target: TargetDescriptor,
    config: TargetConfig,
    scan_run_id: int,
    normalized_reason: str,
    failure_count: int,
    error_message: str,
    target_stopped: bool,
) -> tuple[NotificationOutboxChannelPayload, ...]:
    """依 target config 建立 terminal runtime failure 的 channel payloads。"""

    normalized_count = normalize_runtime_failure_count(failure_count)
    title, message = build_runtime_failure_notification_message(
        target=target,
        reason=normalized_reason,
        failure_count=normalized_count,
        error_message=error_message,
        target_stopped=target_stopped,
    )
    payloads: list[NotificationOutboxChannelPayload] = []
    for plan in build_enabled_channel_plans(config):
        payloads.append(
            NotificationOutboxChannelPayload(
                channel=plan.channel,
                title=title,
                message=(
                    build_runtime_failure_compact_notification_message(message)
                    if plan.use_compact_message
                    else message
                ),
                endpoint=plan.endpoint,
                permalink=target.canonical_url,
                event_kind=NotificationEventKind.RUNTIME_FAILURE,
                source_scan_run_id=scan_run_id,
                failure_reason=normalized_reason,
                failure_count=normalized_count,
            )
        )
    return tuple(payloads)
