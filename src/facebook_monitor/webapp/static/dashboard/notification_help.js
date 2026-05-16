import { bindDialogDismiss, openDialog } from "/static/dashboard/utils.js";

const findHelpModal = (scope, kind) => (
  Array.from(scope?.querySelectorAll("[data-notification-help-modal]") || [])
    .find((modal) => modal.dataset.notificationHelpModal === kind)
);

export const setupNotificationHelp = () => {
  document.querySelectorAll("[data-notification-help-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const scope = button.closest("[data-notification-help-scope]");
      openDialog(findHelpModal(scope, button.dataset.notificationHelpButton || ""));
    });
  });

  bindDialogDismiss({
    modalSelector: "[data-notification-help-modal]",
    closeSelector: "[data-close-notification-help]",
  });
};
