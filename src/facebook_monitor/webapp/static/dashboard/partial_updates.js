import {
  clearTargetUpdatePending,
  isTargetDirty,
  markTargetUpdatePending,
} from "/static/dashboard/state.js";
import { syncNextRefreshCountdown } from "/static/dashboard/next_refresh_countdown.js";
import {
  renderSidebarStatus,
  syncSidebarGroupMonitoringButtons,
} from "/static/dashboard/sidebar_status.js";
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

const updateProfileSessionWarning = (payload) => {
  const warning = document.querySelector("[data-profile-session-warning]");
  if (!warning) return;
  const needsLogin = Boolean(payload?.needs_login);
  warning.textContent = needsLogin ? (payload.message || "") : "";
  warning.toggleAttribute("hidden", !needsLogin);
};

const updateDatabaseInvariantWarning = (payload) => {
  const warning = document.querySelector("[data-database-invariant-warning]");
  if (!warning) return;
  const hasViolations = Boolean(payload?.has_violations);
  warning.textContent = hasViolations ? (payload.message || "") : "";
  warning.toggleAttribute("hidden", !hasViolations);
};

const updateAvatar = (avatar, thumbnailUrl, displayName) => {
  if (!avatar) return;
  const normalizedUrl = String(thumbnailUrl || "").trim();
  if (!normalizedUrl) {
    avatar.classList.remove("has-image");
    avatar.textContent = (displayName || "?").slice(0, 1) || "?";
    return;
  }
  avatar.classList.add("has-image");
  let image = avatar.querySelector("img");
  if (!image) {
    avatar.textContent = "";
    image = document.createElement("img");
    image.alt = "";
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    avatar.append(image);
  }
  if (image.getAttribute("src") !== normalizedUrl) {
    image.src = normalizedUrl;
  }
};

const updateStatusBadge = (card, payload) => {
  const status = card.querySelector("[data-card-status]");
  if (!status) return;
  status.className = `status ${payload.status_class || ""}`.trim();
  status.textContent = payload.status_label || "";
};

const updateHeaderRuntimeSummary = (card, payload) => {
  const mode = card.querySelector("[data-target-mode]");
  if (mode) {
    mode.className = `target-mode-chip ${payload.mode_class || ""}`.trim();
    mode.textContent = payload.mode_label || "";
  }
  updateText(card, "[data-latest-scan-header]", payload.latest_scan_header_label || "");
  syncNextRefreshCountdown(
    card.querySelector("[data-next-refresh]"),
    payload.next_refresh_seconds,
    payload.next_refresh_label || "",
  );
  const showLatestError = Boolean(payload.has_latest_failed_scan);
  card.querySelector("[data-latest-error-separator]")?.toggleAttribute(
    "hidden",
    !showLatestError,
  );
  card.querySelector("[data-latest-error-indicator]")?.toggleAttribute(
    "hidden",
    !showLatestError,
  );
  const latestErrorIndicator = card.querySelector("[data-latest-error-indicator]");
  if (latestErrorIndicator) {
    latestErrorIndicator.textContent = payload.latest_error_indicator_label || "最近有錯誤";
    latestErrorIndicator.title = payload.latest_error_indicator_title || "";
    latestErrorIndicator.dataset.latestErrorKind = payload.latest_error_indicator_kind || "";
  }
};

const updateMonitoringAction = (card, payload) => {
  const form = card.querySelector("[data-monitoring-form]");
  if (form && payload.monitoring_action) {
    form.action = `/targets/${encodeURIComponent(payload.target_id)}/${payload.monitoring_action}`;
  }
  const button = card.querySelector("[data-monitoring-button]");
  if (button) {
    button.textContent = payload.monitoring_button_label || "";
  }
};

const updateRuntimeMessage = (card, selector, value) => {
  const node = card.querySelector(selector);
  if (!node) return;
  const text = String(value || "");
  node.textContent = text;
  node.hidden = !text;
};

const updateRuntimeMessages = (card, payload) => {
  updateRuntimeMessage(card, "[data-runtime-error]", payload.runtime_error);
  updateRuntimeMessage(card, "[data-runtime-skip-reason]", payload.runtime_skip_reason);
};

const updateScanCycleResult = (card, payload) => {
  const result = card.querySelector("[data-scan-cycle-result]");
  if (!result) return;
  const label = payload.scan_cycle_result_label || "";
  result.textContent = label;
  result.title = label;
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

const updateRenameInput = (card, payload) => {
  const input = card.querySelector('[data-rename-target-modal] input[name="display_name"]');
  if (!input || document.activeElement === input) return;
  const nextValue = String(payload.rename_display_name ?? payload.display_name ?? "");
  input.value = nextValue;
  input.setAttribute("value", nextValue);
};

const updateSidebar = (payload) => {
  const sidebarRoot = document.querySelector("[data-sidebar-layout]");
  const expectedSignature = String(payload.layout_signature || "");
  const expectedTemplateSignature = String(payload.template_signature || "");
  if (
    expectedSignature
    && sidebarRoot?.dataset.sidebarLayoutSignature
    && sidebarRoot.dataset.sidebarLayoutSignature !== expectedSignature
  ) {
    return false;
  }
  if (
    expectedTemplateSignature
    && sidebarRoot?.dataset.sidebarTemplateSignature
    && sidebarRoot.dataset.sidebarTemplateSignature !== expectedTemplateSignature
  ) {
    return false;
  }
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
    const sidebarItem = status.closest("[data-sidebar-item]");
    if (sidebarItem) {
      sidebarItem.dataset.sidebarItemActive = item.active ? "1" : "0";
    }
    updateAvatar(avatar, item.thumbnail_url, item.display_name);
    renderSidebarStatus(status, {
      baseStatus: item.base_status_summary || "",
      statusClass: item.status_class || "",
      statusDetail: item.status_detail || "",
      defaultDetail: item.status_detail || "",
      modeLabel: item.mode_label || "",
      modeClass: item.mode_class || "",
    });
  });
  syncSidebarGroupMonitoringButtons();
  return allItemsMatched;
};

const updateTargetCard = (state, payload) => {
  const card = document.querySelector(`[data-target-card][data-target-id="${payload.target_id}"]`);
  if (!card) return;

  updateStatusBadge(card, payload);
  updateText(card, "[data-target-title]", payload.display_name || "");
  updateAvatar(
    card.querySelector("[data-target-avatar]"),
    payload.thumbnail_url,
    payload.display_name,
  );
  updateRenameInput(card, payload);
  updateHeaderRuntimeSummary(card, payload);
  updateMonitoringAction(card, payload);
  updateRuntimeMessages(card, payload);
  updateScanCycleResult(card, payload);
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

const orderedTargetIds = (nodes) => nodes.map((node) => node.dataset.targetId || "");

const sameOrder = (left, right) => (
  left.length === right.length && left.every((value, index) => value === right[index])
);

export const applyDashboardPartialUpdate = async (state) => {
  const sequence = state.partialUpdateSeq + 1;
  state.partialUpdateSeq = sequence;
  state.partialUpdateInFlight = true;

  try {
    const dashboardPayload = await fetchJson("/api/dashboard-cards");
    if (sequence !== state.partialUpdateSeq) return;
    updateProfileSessionWarning(dashboardPayload.profile_session_warning || {});
    updateDatabaseInvariantWarning(dashboardPayload.database_invariant_warning || {});
    const currentDashboardDegraded = Boolean(document.querySelector("[data-dashboard-degraded-empty]"));
    if (Boolean(dashboardPayload.dashboard_degraded) !== currentDashboardDegraded) {
      throw new Error("partial_update_requires_reload:dashboard_degraded_changed");
    }
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
    const payloadOrder = cardPayloads.map((payload) => payload.target_id || "");
    if (!sameOrder(orderedTargetIds(targetCards), payloadOrder)) {
      throw new Error("partial_update_requires_reload:card_order_changed");
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
