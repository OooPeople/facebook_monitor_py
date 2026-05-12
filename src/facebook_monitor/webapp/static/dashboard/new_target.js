import { setupRefreshFields } from "/static/dashboard/forms.js";
import { setupThemeToggle } from "/static/dashboard/theme.js";

setupThemeToggle();
setupRefreshFields();

document.querySelectorAll("[data-new-target-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.textContent = button.dataset.loadingText || "建立中...";
    });
  });
});
