import { csrfHeaders } from "/static/dashboard/csrf.js";
import { showInlineStatus } from "/static/dashboard/utils.js";

const readJsonResponse = async (response) => {
  try {
    return await response.json();
  } catch (error) {
    return {};
  }
};

const statusElementFor = (button) => (
  button.closest(".notification-test-actions")?.querySelector("[data-notification-test-status]")
);

const setButtonBusy = (button, busy) => {
  if (!button) return;
  button.disabled = busy;
  button.setAttribute("aria-busy", busy ? "true" : "false");
};

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
    showInlineStatus(statusElement, "正在發送...", "dirty");
    try {
      const response = await fetch(action, {
        method: "POST",
        body: new FormData(form),
        headers: csrfHeaders({ Accept: "application/json" }),
      });
      const payload = await readJsonResponse(response);
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || `測試通知失敗：HTTP ${response.status}`);
      }
      showInlineStatus(statusElement, payload.message || "測試通知已發送", "saved", 3500);
    } catch (error) {
      showInlineStatus(statusElement, error?.message || "測試通知失敗", "dirty", 5000);
    } finally {
      setButtonBusy(button, false);
    }
  });
};
