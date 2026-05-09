import { closeDialog, openDialog } from "./utils.js";

export const setupSettingsModals = () => {
  document.querySelectorAll("[data-settings-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.targetAnchor || "");
      const modal = target?.querySelector("[data-settings-modal]");
      openDialog(modal);
    });
  });

  document.querySelectorAll("[data-close-settings]").forEach((button) => {
    button.addEventListener("click", () => {
      closeDialog(button.closest("[data-settings-modal]"));
    });
  });

  document.querySelectorAll("[data-settings-modal]").forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeDialog(modal);
      }
    });
  });
};
