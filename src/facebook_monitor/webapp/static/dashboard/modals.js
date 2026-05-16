import { bindDialogDismiss, openDialog } from "/static/dashboard/utils.js";

export const setupSettingsModals = () => {
  document.querySelectorAll("[data-rename-target-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const card = button.closest("[data-target-card]");
      const modal = card?.querySelector("[data-rename-target-modal]");
      button.closest(".more-menu")?.removeAttribute("open");
      openDialog(modal);
      modal?.querySelector('input[name="display_name"]')?.select();
    });
  });

  document.querySelectorAll("[data-scan-diagnostics-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const card = button.closest("[data-target-card]");
      const modal = card?.querySelector("[data-scan-diagnostics-modal]");
      button.closest(".more-menu")?.removeAttribute("open");
      openDialog(modal);
    });
  });

  bindDialogDismiss({
    modalSelector: "[data-scan-diagnostics-modal]",
    closeSelector: "[data-close-scan-diagnostics]",
  });

  bindDialogDismiss({
    modalSelector: "[data-rename-target-modal]",
    closeSelector: "[data-close-rename-target]",
  });

  document.querySelectorAll("[data-settings-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.targetAnchor || "");
      const modal = target?.querySelector("[data-settings-modal]");
      openDialog(modal);
    });
  });

  bindDialogDismiss({
    modalSelector: "[data-settings-modal]",
    closeSelector: "[data-close-settings]",
  });

  document.querySelectorAll("[data-include-keyword-help-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const modal = button.closest("[data-target-card]")?.querySelector("[data-include-keyword-help-modal]");
      openDialog(modal);
    });
  });

  bindDialogDismiss({
    modalSelector: "[data-include-keyword-help-modal]",
    closeSelector: "[data-close-include-keyword-help]",
  });

  document.querySelectorAll("[data-keyword-help-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const modal = button.closest("[data-target-card]")?.querySelector("[data-keyword-help-modal]");
      openDialog(modal);
    });
  });

  bindDialogDismiss({
    modalSelector: "[data-keyword-help-modal]",
    closeSelector: "[data-close-keyword-help]",
  });
};
