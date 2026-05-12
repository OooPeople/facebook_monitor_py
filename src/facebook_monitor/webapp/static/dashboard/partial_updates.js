import {
  clearTargetUpdatePending,
  isTargetDirty,
  markTargetUpdatePending,
} from "/static/dashboard/state.js";
import { renderSidebarStatus } from "/static/dashboard/sidebar_status.js";
import { showInlineStatus } from "/static/dashboard/utils.js";

const fetchJson = async (url) => {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`partial_update_failed:${response.status}:${url}`);
  }
  return response.json();
};

const updateText = (root, selector, value) => {
  const element = root.querySelector(selector);
  if (element) {
    element.textContent = String(value ?? "");
  }
};

const updateStatusBadge = (card, payload) => {
  const status = card.querySelector("[data-card-status]");
  if (!status) return;
  status.className = `status ${payload.status_class || ""}`.trim();
  status.textContent = payload.status_label || "";
};

const updateCollapsedSummary = (card, payload) => {
  const summary = card.querySelector("[data-collapsed-summary]");
  if (!summary) return;
  summary.innerHTML = payload.card_summary_html || "";
};

const updatePreviewPanel = (card, panelName, html) => {
  const panel = card.querySelector(`[data-preview-panel="${panelName}"]`);
  if (!panel) return;
  panel.innerHTML = html || "";
};

const updateScanDiagnostics = (card, payload) => {
  updateText(
    card,
    ".scan-debug-details .debug-summary",
    payload.latest_scan_diagnostics_summary || "",
  );
  const source = card.querySelector(".scan-debug-details .debug-copy-source");
  if (source) {
    source.value = payload.latest_scan_diagnostics_text || "";
    source.textContent = payload.latest_scan_diagnostics_text || "";
  }
};

const updateSidebar = (payload) => {
  let allItemsMatched = true;
  (payload.items || []).forEach((item) => {
    const status = document.querySelector(`[data-sidebar-status="${item.target_id}"]`);
    if (!status) {
      allItemsMatched = false;
      return;
    }
    const link = status.closest("[data-sidebar-target]");
    const name = link?.querySelector(".sidebar-name");
    const avatar = link?.querySelector(".sidebar-avatar");
    if (name) {
      name.textContent = item.display_name || "";
    }
    if (avatar) {
      avatar.textContent = (item.display_name || "?").slice(0, 1) || "?";
    }
    renderSidebarStatus(status, {
      baseStatus: item.base_status_summary || "",
      statusClass: item.status_class || "",
      statusDetail: item.status_detail || "",
      defaultDetail: item.status_detail || "",
    });
  });
  return allItemsMatched;
};

const updateTargetCard = (state, payload) => {
  const card = document.querySelector(`[data-target-card][data-target-id="${payload.target_id}"]`);
  if (!card) return;

  updateStatusBadge(card, payload);
  updateText(card, "[data-target-title]", payload.display_name || "");
  updateText(card, "[data-target-avatar]", (payload.display_name || "?").slice(0, 1) || "?");
  updateText(card, "[data-header-summary]", payload.header_summary_label);
  updateCollapsedSummary(card, payload);
  updateText(card, `[data-hit-count="${payload.target_id}"]`, payload.hit_record_total_count);
  updateScanDiagnostics(card, payload);

  if (isTargetDirty(state, payload.target_id)) {
    markTargetUpdatePending(state, payload.target_id);
    showInlineStatus(
      card.querySelector("[data-dirty-status]"),
      "背景資料已更新，儲存後會套用最新顯示",
      "dirty",
    );
    return;
  }

  updatePreviewPanel(
    card,
    "latest",
    payload.latest_scan_preview_html,
  );
  updatePreviewPanel(
    card,
    "hits",
    payload.hit_record_preview_html,
  );
  clearTargetUpdatePending(state, payload.target_id);
};

export const applyDashboardPartialUpdate = async (state) => {
  const sequence = state.partialUpdateSeq + 1;
  state.partialUpdateSeq = sequence;
  state.partialUpdateInFlight = true;

  try {
    const dashboardPayload = await fetchJson("/api/dashboard-cards");
    if (sequence !== state.partialUpdateSeq) return;
    const targetCards = Array.from(document.querySelectorAll("[data-target-card][data-target-id]"));
    const sidebarPayload = dashboardPayload.sidebar || {};
    if (
      !updateSidebar(sidebarPayload)
      || (sidebarPayload.items || []).length !== targetCards.length
    ) {
      throw new Error("partial_update_requires_reload:target_list_changed");
    }

    const cardPayloads = dashboardPayload.cards || [];
    if (cardPayloads.length !== targetCards.length) {
      throw new Error("partial_update_requires_reload:card_count_changed");
    }
    const targetCardIds = new Set(targetCards.map((card) => card.dataset.targetId || ""));
    if (!cardPayloads.every((payload) => targetCardIds.has(payload.target_id || ""))) {
      throw new Error("partial_update_requires_reload:card_ids_changed");
    }
    cardPayloads.forEach((payload) => {
      if (sequence === state.partialUpdateSeq) {
        updateTargetCard(state, payload);
      }
    });
  } finally {
    if (sequence === state.partialUpdateSeq) {
      state.partialUpdateInFlight = false;
    }
  }
};
