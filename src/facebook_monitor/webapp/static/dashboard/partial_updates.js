import {
  clearTargetUpdatePending,
  isTargetDirty,
  markTargetUpdatePending,
} from "./state.js";
import { renderPreviewRows } from "./render_preview_rows.js";
import { renderSidebarStatus } from "./sidebar_status.js?v=ui-refactor-phase18-form-sidebar-status";
import { showInlineStatus } from "./utils.js";

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
  summary.replaceChildren();
  const sections = payload.card_summary?.sections || [];
  if (sections.length) {
    sections.forEach((section) => {
      const field = document.createElement("div");
      field.className = "target-collapsed-summary-field field-grid-cell";

      const label = document.createElement("dt");
      label.textContent = section.label || "";
      field.appendChild(label);

      const value = document.createElement("dd");
      (section.lines || []).forEach((line) => {
        const item = document.createElement("span");
        item.textContent = line;
        value.appendChild(item);
      });
      field.appendChild(value);
      summary.appendChild(field);
    });
    return;
  }

  (payload.card_summary?.lines || []).forEach((line) => {
    const field = document.createElement("div");
    field.className = "target-collapsed-summary-field field-grid-cell";
    const value = document.createElement("dd");
    const item = document.createElement("span");
    item.textContent = line;
    value.appendChild(item);
    field.appendChild(value);
    summary.appendChild(field);
  });
};

const updatePreviewPanel = (card, panelName, rows, emptyText) => {
  const panel = card.querySelector(`[data-preview-panel="${panelName}"]`);
  if (!panel) return;
  panel.replaceChildren(renderPreviewRows(rows || [], emptyText));
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
    payload.latest_scan_preview_rows,
    "尚無掃描紀錄",
  );
  updatePreviewPanel(
    card,
    "hits",
    payload.hit_record_preview_rows,
    "尚無命中紀錄",
  );
  clearTargetUpdatePending(state, payload.target_id);
};

export const applyDashboardPartialUpdate = async (state) => {
  const sequence = state.partialUpdateSeq + 1;
  state.partialUpdateSeq = sequence;
  state.partialUpdateInFlight = true;

  try {
    const sidebarPayload = await fetchJson("/api/sidebar");
    if (sequence !== state.partialUpdateSeq) return;
    const targetCards = Array.from(document.querySelectorAll("[data-target-card][data-target-id]"));
    if (
      !updateSidebar(sidebarPayload)
      || (sidebarPayload.items || []).length !== targetCards.length
    ) {
      throw new Error("partial_update_requires_reload:target_list_changed");
    }

    await Promise.all(
      targetCards.map(async (card) => {
        const targetId = card.dataset.targetId || "";
        if (!targetId) return;
        const payload = await fetchJson(`/api/targets/${targetId}/card`);
        if (sequence === state.partialUpdateSeq) {
          updateTargetCard(state, payload);
        }
      }),
    );
  } finally {
    if (sequence === state.partialUpdateSeq) {
      state.partialUpdateInFlight = false;
    }
  }
};
