import { animateElementVisibility } from "/static/dashboard/collapse_animation.js";
import { setupRefreshFields, setupSecretClearButtons } from "/static/dashboard/forms.js";
import { setupNotificationHelp } from "/static/dashboard/notification_help.js";
import { setupThemeToggle } from "/static/dashboard/theme.js";

const syncAdvancedToggleState = (details, expanded) => {
  const toggle = details.querySelector("[data-new-target-advanced-toggle]");
  if (!toggle) return;

  toggle.setAttribute("aria-expanded", String(expanded));
  details.classList.toggle("is-expanded", expanded);
  const label = expanded ? "收合進階設定" : "展開進階設定";
  toggle.setAttribute("title", label);
};

const setAdvancedExpandedState = (details, expanded, { animate = false } = {}) => {
  const body = details.querySelector("[data-new-target-advanced-body]");
  if (!body) return;

  syncAdvancedToggleState(details, expanded);

  if (!animate) {
    details.open = expanded;
    body.hidden = !expanded;
    return;
  }

  if (expanded) {
    details.open = true;
    animateElementVisibility(body, true);
    return;
  }

  animateElementVisibility(body, false, {
    afterFinish: () => {
      const toggle = details.querySelector("[data-new-target-advanced-toggle]");
      if (toggle?.getAttribute("aria-expanded") !== "true") {
        details.open = false;
      }
    },
  });
};

const setupNewTargetAdvancedCollapse = () => {
  document.querySelectorAll("[data-new-target-advanced]").forEach((details) => {
    const toggle = details.querySelector("[data-new-target-advanced-toggle]");
    if (!toggle) return;
    setAdvancedExpandedState(details, details.open);
    details.addEventListener("toggle", () => {
      const body = details.querySelector("[data-new-target-advanced-body]");
      syncAdvancedToggleState(details, details.open);
      if (body && !body.hasAttribute("data-collapse-animating")) {
        body.hidden = !details.open;
      }
    });
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      setAdvancedExpandedState(details, !expanded, { animate: true });
    });
  });
};

setupThemeToggle();
setupRefreshFields();
setupSecretClearButtons();
setupNotificationHelp();
setupNewTargetAdvancedCollapse();

document.querySelectorAll("[data-new-target-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    form.setAttribute("aria-busy", "true");
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = true;
      button.textContent = button.dataset.loadingText || "建立中...";
    });
  });
});
