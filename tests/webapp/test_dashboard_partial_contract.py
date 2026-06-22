"""Dashboard partial update static contract tests。

這組 tests 鎖住 Python serializer、static JS consumed keys 與 server-rendered
DOM anchors 的 drift；它不取代 browser-level partial update behavior tests。
若 frontend 改用 destructuring、bracket access 或不同 payload variable name，
需同步更新 helper。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient

from facebook_monitor.application.context import SqliteApplicationContext
from facebook_monitor.application.target_requests import UpsertGroupPostsTargetRequest
from tests.helpers.webapp import seed_dashboard_index_target
from tests.webapp.app_test_helpers import create_app
from tests.webapp.static_contract_helpers import sidebar_template_family_text
from tests.webapp.static_contract_helpers import target_card_template_family_text


DASHBOARD_PAYLOADS_PATH = Path("src/facebook_monitor/webapp/dashboard_payloads.py")
PARTIAL_UPDATES_JS_PATH = Path(
    "src/facebook_monitor/webapp/static/dashboard/partial_updates.js"
)


def _serializer_return_keys(function_name: str) -> set[str]:
    """讀取 serializer 回傳 dict 的固定 key，避免手寫契約漂移。"""

    tree = ast.parse(DASHBOARD_PAYLOADS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and isinstance(child.value, ast.Dict):
                keys = {
                    key.value
                    for key in child.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                if keys:
                    return keys
    raise AssertionError(f"missing serializer return dict: {function_name}")


def _js_property_names(root_name: str) -> set[str]:
    """擷取 static JS 對物件欄位的 dot / optional-chain 讀取。

    這是 partial update payload drift guard，不是完整 JS parser；若 frontend
    改用 destructuring / bracket notation，需同步更新本 helper。
    """

    text = PARTIAL_UPDATES_JS_PATH.read_text(encoding="utf-8")
    return set(
        re.findall(
            rf"\b{re.escape(root_name)}\??\.([A-Za-z_][A-Za-z0-9_]*)",
            text,
        )
    )


def test_dashboard_card_partial_routes_share_serializer_contract(tmp_path: Path) -> None:
    """batch card 與單卡 partial route 對同一 target 必須產生同一份 card contract。"""

    db_path = tmp_path / "app.db"
    target = seed_dashboard_index_target(db_path)
    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    batch_payload = client.get("/api/dashboard-cards").json()
    single_card_payload = client.get(f"/api/targets/{target.id}/card").json()

    assert len(batch_payload["cards"]) == 1
    batch_card_payload = batch_payload["cards"][0]
    assert batch_card_payload == single_card_payload
    assert set(batch_card_payload) == _serializer_return_keys("serialize_target_card")


def test_dashboard_batch_keeps_sidebar_and_card_targets_aligned(tmp_path: Path) -> None:
    """dashboard batch 內 sidebar/card target id 必須同序，前端才可安全局部更新。"""

    db_path = tmp_path / "app.db"
    with SqliteApplicationContext(db_path) as app_context:
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="111",
                canonical_url="https://www.facebook.com/groups/111",
                group_name="第一個社團",
            )
        )
        app_context.services.targets.upsert_group_posts_target(
            UpsertGroupPostsTargetRequest(
                group_id="222",
                canonical_url="https://www.facebook.com/groups/222",
                group_name="第二個社團",
            )
        )

    client = TestClient(create_app(db_path=db_path, profile_dir=tmp_path / "profile"))

    payload = client.get("/api/dashboard-cards").json()

    assert [item["target_id"] for item in payload["sidebar"]["items"]] == [
        card["target_id"] for card in payload["cards"]
    ]


def test_partial_update_frontend_card_keys_are_serialized() -> None:
    """partial_updates.js 不可消費 serialize_target_card 沒有提供的 card 欄位。"""

    serialized_card_keys = _serializer_return_keys("serialize_target_card")
    non_card_payload_keys = {
        "has_violations",
        "items",
        "layout_signature",
        "message",
        "needs_login",
        "template_signature",
    }
    consumed_card_keys = _js_property_names("payload") - non_card_payload_keys

    assert consumed_card_keys <= serialized_card_keys
    assert {
        "target_id",
        "display_name",
        "status_label",
        "status_class",
        "mode_label",
        "mode_class",
        "monitoring_action",
        "monitoring_button_label",
        "latest_scan_preview_html",
        "hit_record_preview_html",
        "latest_scan_diagnostics_text",
        "hit_record_total_count",
    } <= consumed_card_keys


def test_partial_update_frontend_sidebar_item_keys_are_serialized() -> None:
    """sidebar partial update 不可消費 serialize_sidebar_item 沒有提供的欄位。"""

    serialized_sidebar_item_keys = _serializer_return_keys("serialize_sidebar_item")
    consumed_sidebar_item_keys = _js_property_names("item")

    assert consumed_sidebar_item_keys <= serialized_sidebar_item_keys
    assert {
        "target_id",
        "display_name",
        "active",
        "thumbnail_url",
        "base_status_summary",
        "status_class",
        "status_detail",
        "mode_label",
        "mode_class",
    } <= consumed_sidebar_item_keys


def test_partial_update_dom_anchors_exist_in_server_templates() -> None:
    """前端 partial update selector 必須有 server-rendered DOM anchor。"""

    card_template = target_card_template_family_text()
    sidebar_template = sidebar_template_family_text()

    for snippet in (
        "data-target-card",
        "data-target-id",
        "data-card-status",
        "data-target-title",
        "data-target-avatar",
        "data-target-mode",
        "data-latest-scan-header",
        "data-next-refresh",
        "data-latest-error-indicator",
        "data-monitoring-form",
        "data-monitoring-button",
        "data-runtime-error",
        "data-runtime-skip-reason",
        "data-scan-cycle-result",
        "data-collapsed-summary",
        "data-preview-panel=\"latest\"",
        "data-preview-panel=\"hits\"",
        "data-rename-target-modal",
    ):
        assert snippet in card_template
    assert "data-hit-count=\"{{ row.target.id }}\"" in card_template

    for snippet in (
        "data-sidebar-layout",
        "data-sidebar-layout-signature",
        "data-sidebar-template-signature",
        "data-sidebar-item",
        "data-sidebar-item-active",
        "data-sidebar-status",
        "class=\"sidebar-name\"",
        "class=\"sidebar-avatar",
    ):
        assert snippet in sidebar_template


def test_partial_update_reload_guards_cover_batch_shape_changes() -> None:
    """前端遇到 batch 結構變更必須要求 reload，而不是嘗試局部套用。"""

    partial_updates_js = PARTIAL_UPDATES_JS_PATH.read_text(encoding="utf-8")

    assert "partial_update_requires_reload:dashboard_degraded_changed" in partial_updates_js
    assert "partial_update_requires_reload:target_list_changed" in partial_updates_js
    assert "partial_update_requires_reload:card_count_changed" in partial_updates_js
    assert "partial_update_requires_reload:card_ids_changed" in partial_updates_js
    assert "partial_update_requires_reload:card_order_changed" in partial_updates_js
    assert "sameOrder(orderedTargetIds(targetCards), payloadOrder)" in partial_updates_js
