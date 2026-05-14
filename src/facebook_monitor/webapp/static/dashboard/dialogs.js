import { closeDialog, openDialog } from "/static/dashboard/utils.js";

const removeDialog = (dialog) => {
  if (!dialog) return;
  closeDialog(dialog);
  dialog.remove();
};

const createDialogShell = ({ title, danger = false, showHeaderClose = false }) => {
  const dialog = document.createElement("dialog");
  dialog.className = "settings-modal app-dialog";
  const shell = document.createElement("div");
  shell.className = "modal-shell";

  const header = document.createElement("header");
  header.className = "modal-header";
  const titleBlock = document.createElement("div");
  const heading = document.createElement("h3");
  heading.textContent = title || "確認操作";
  titleBlock.appendChild(heading);
  header.appendChild(titleBlock);
  if (showHeaderClose) {
    const closeButton = document.createElement("button");
    closeButton.className = "button--icon";
    closeButton.type = "button";
    closeButton.appendChild(createCloseIcon());
    closeButton.setAttribute("aria-label", "關閉");
    closeButton.addEventListener("click", () => {
      dialog.dispatchEvent(new CustomEvent("app-dialog-cancel"));
    });
    header.appendChild(closeButton);
  }

  const body = document.createElement("div");
  body.className = `modal-body app-dialog-body${danger ? " danger" : ""}`;

  const footer = document.createElement("footer");
  footer.className = "modal-footer";
  const spacer = document.createElement("span");
  spacer.className = "field-caption";
  const actions = document.createElement("div");
  actions.className = "modal-footer-actions";
  footer.appendChild(spacer);
  footer.appendChild(actions);

  shell.appendChild(header);
  shell.appendChild(body);
  shell.appendChild(footer);
  dialog.appendChild(shell);

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.dispatchEvent(new CustomEvent("app-dialog-cancel"));
    }
  });
  document.body.appendChild(dialog);
  return { dialog, body, actions };
};

const createSvgElement = (tagName) => (
  document.createElementNS("http://www.w3.org/2000/svg", tagName)
);

const createCloseIcon = () => {
  const icon = createSvgElement("svg");
  icon.setAttribute("class", "ui-icon");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  const first = createSvgElement("path");
  first.setAttribute("d", "M18 6 6 18");
  const second = createSvgElement("path");
  second.setAttribute("d", "m6 6 12 12");
  icon.appendChild(first);
  icon.appendChild(second);
  return icon;
};

export const confirmDialog = ({
  title,
  message,
  confirmLabel = "確認",
  cancelLabel = "取消",
  danger = false,
} = {}) => new Promise((resolve) => {
  const { dialog, body, actions } = createDialogShell({ title, danger });
  if (message) {
    const messageNode = document.createElement("p");
    messageNode.textContent = message;
    body.appendChild(messageNode);
  }

  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.textContent = cancelLabel;
  const confirmButton = document.createElement("button");
  confirmButton.type = "button";
  confirmButton.className = danger ? "button--danger" : "button button--primary";
  confirmButton.textContent = confirmLabel;
  actions.appendChild(cancelButton);
  actions.appendChild(confirmButton);

  let settled = false;
  const finish = (value) => {
    if (settled) return;
    settled = true;
    resolve(value);
    removeDialog(dialog);
  };
  cancelButton.addEventListener("click", () => finish(false));
  confirmButton.addEventListener("click", () => finish(true));
  dialog.addEventListener("cancel", () => finish(false), { once: true });
  dialog.addEventListener("close", () => finish(false), { once: true });
  dialog.addEventListener("app-dialog-cancel", () => finish(false), { once: true });
  openDialog(dialog);
  confirmButton.focus();
});

export const promptDialog = ({
  title,
  message,
  label = "名稱",
  value = "",
  confirmLabel = "確認",
  cancelLabel = "取消",
} = {}) => new Promise((resolve) => {
  const { dialog, body, actions } = createDialogShell({ title });
  if (message) {
    const messageNode = document.createElement("p");
    messageNode.textContent = message;
    body.appendChild(messageNode);
  }
  const field = document.createElement("label");
  field.textContent = label;
  const input = document.createElement("input");
  input.type = "text";
  input.value = value || "";
  input.autocomplete = "off";
  field.appendChild(input);
  body.appendChild(field);

  const cancelButton = document.createElement("button");
  cancelButton.type = "button";
  cancelButton.textContent = cancelLabel;
  const confirmButton = document.createElement("button");
  confirmButton.type = "button";
  confirmButton.className = "button button--primary";
  confirmButton.textContent = confirmLabel;
  actions.appendChild(cancelButton);
  actions.appendChild(confirmButton);

  let settled = false;
  const finish = (nextValue) => {
    if (settled) return;
    settled = true;
    resolve(nextValue);
    removeDialog(dialog);
  };
  cancelButton.addEventListener("click", () => finish(null));
  confirmButton.addEventListener("click", () => finish(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(input.value);
    }
  });
  dialog.addEventListener("cancel", () => finish(null), { once: true });
  dialog.addEventListener("close", () => finish(null), { once: true });
  dialog.addEventListener("app-dialog-cancel", () => finish(null), { once: true });
  openDialog(dialog);
  input.focus();
  input.select();
});

export const setupConfirmSubmitForms = () => {
  document.querySelectorAll("[data-confirm-submit]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      if (form.dataset.confirmedSubmit === "1") {
        delete form.dataset.confirmedSubmit;
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      const confirmed = await confirmDialog({
        title: form.dataset.confirmTitle || "確認操作",
        message: form.dataset.confirmMessage || "",
        confirmLabel: form.dataset.confirmLabel || "確認",
        danger: form.dataset.confirmDanger === "1",
      });
      if (!confirmed) return;
      form.dataset.confirmedSubmit = "1";
      form.requestSubmit();
    });
  });
};
