import { csrfHeaders } from "/static/dashboard/csrf.js";
import {
  formatClientErrorMessage,
  showInlineStatus,
} from "/static/dashboard/utils.js";

const readJsonResponse = async (response) => {
  try {
    return await response.json();
  } catch (error) {
    return {};
  }
};

const statusElementFor = (button) => (
  button.closest(".notification-test-actions")
    ?.parentElement
    ?.querySelector("[data-notification-test-status]")
);

const setButtonBusy = (button, busy) => {
  if (!button) return;
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
};

const notificationTestStatusKind = (payload) => (
  payload?.tone === "success" && payload?.all_ok !== false ? "saved" : "dirty"
);

const notificationTestTimeoutMs = (payload, fallback) => {
  if (payload?.sticky === true) return 0;
  const value = Number(payload?.timeout_ms);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
};

const notificationTestMessage = (payload, fallback) => (
  payload?.message || payload?.error || fallback
);

export const setupNotificationTest = () => {
  document.addEventListener("click", async (event) => {
    const button = event.target.closest?.("[data-notification-test]");
    if (!button) return;
    event.preventDefault();

    const form = document.getElementById(button.dataset.notificationTestFormId || "");
    const statusElement = statusElementFor(button);
    const action = button.dataset.notificationTestAction || "";
    if (!form || !action) return;

    setButtonBusy(button, true);
    showInlineStatus(statusElement, "正在發送...", "dirty", { timeoutMs: 0 });
    try {
      const response = await fetch(action, {
        method: "POST",
        body: new FormData(form),
        headers: csrfHeaders({ Accept: "application/json" }),
      });
      const payload = await readJsonResponse(response);
      if (!response.ok || payload.ok === false) {
        showInlineStatus(
          statusElement,
          notificationTestMessage(payload, `測試通知失敗：HTTP ${response.status}`),
          notificationTestStatusKind(payload),
          { timeoutMs: notificationTestTimeoutMs(payload, 5000) },
        );
        return;
      }
      showInlineStatus(
        statusElement,
        notificationTestMessage(payload, "測試通知已發送"),
        notificationTestStatusKind(payload),
        { timeoutMs: notificationTestTimeoutMs(payload, 3500) },
      );
    } catch (error) {
      showInlineStatus(
        statusElement,
        formatClientErrorMessage(error, "測試通知失敗"),
        "dirty",
        { timeoutMs: 5000 },
      );
    } finally {
      setButtonBusy(button, false);
    }
  });
};
