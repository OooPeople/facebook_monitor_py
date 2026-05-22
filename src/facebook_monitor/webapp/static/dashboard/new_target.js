import { setupRefreshFields, setupSecretClearButtons } from "/static/dashboard/forms.js";
import { setupNotificationHelp } from "/static/dashboard/notification_help.js";
import { setupThemeToggle } from "/static/dashboard/theme.js";

setupThemeToggle();
setupRefreshFields();
setupSecretClearButtons();
setupNotificationHelp();

document.querySelectorAll("[data-new-target-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.textContent = button.dataset.loadingText || "建立中...";
    });
  });
});
