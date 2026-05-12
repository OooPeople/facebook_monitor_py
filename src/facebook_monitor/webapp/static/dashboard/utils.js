export const editableSelector = "input, textarea, select";

export const getFormControls = (form) => {
  if (!form) return [];
  return Array.from(form.elements || []).filter((node) =>
    node.matches?.(editableSelector)
  );
};

export const setupDirtyFormStatus = ({
  form,
  statusElement,
  dirtyText = "尚未儲存",
  onDirtyChange = () => {},
}) => {
  if (!form) return;

  const formId = form.id || "";
  const submitButtons = () => [
    ...Array.from(form.querySelectorAll("[data-dirty-submit]")),
    ...(
      formId
        ? Array.from(document.querySelectorAll(`[form="${CSS.escape(formId)}"][data-dirty-submit]`))
        : []
    ),
  ];
  const controlSignature = () => JSON.stringify(
    getFormControls(form).map((node) => ({
      name: node.name || "",
      type: node.type || "",
      value: node.type === "checkbox" || node.type === "radio"
        ? Boolean(node.checked)
        : node.value,
    })),
  );
  const baseline = controlSignature();

  const updateDirtyState = () => {
    const dirty = controlSignature() !== baseline;
    form.classList.toggle("is-dirty", dirty);
    submitButtons().forEach((button) => {
      button.classList.toggle("is-dirty", dirty);
    });
    onDirtyChange(dirty);
    if (dirty) {
      showInlineStatus(statusElement, dirtyText, "dirty");
    } else if (statusElement?.dataset.statusKind === "dirty") {
      statusElement.classList.remove("is-visible");
      delete statusElement.dataset.statusKind;
    }
  };
  getFormControls(form).forEach((node) => {
    node.addEventListener("input", updateDirtyState);
    node.addEventListener("change", updateDirtyState);
  });
  updateDirtyState();
};

export const readJsonScript = (id, fallback) => {
  const node = document.getElementById(id);
  if (!node) return fallback;
  return JSON.parse(node.textContent || JSON.stringify(fallback));
};

export const showInlineStatus = (node, text, kind, timeoutMs = 0) => {
  if (!node) return;
  node.textContent = text;
  node.dataset.statusKind = kind;
  node.classList.add("is-visible");
  if (timeoutMs > 0) {
    window.setTimeout(() => {
      if (document.body.contains(node) && node.dataset.statusKind === kind) {
        node.classList.remove("is-visible");
      }
    }, timeoutMs);
  }
};

export const showToast = (text, kind = "success") => {
  if (!text) return;
  const stack = document.querySelector(".toast-stack");
  if (!stack) return;
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.textContent = text;
  stack.appendChild(toast);
  window.setTimeout(() => {
    if (document.body.contains(toast)) {
      toast.remove();
    }
  }, 3500);
};

export const closeDialog = (modal) => {
  if (!modal) return;
  if (typeof modal.close === "function") {
    modal.close();
  } else {
    modal.removeAttribute("open");
  }
};

export const openDialog = (modal) => {
  if (!modal) return;
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.setAttribute("open", "");
  }
};

export const bindDialogDismiss = ({ modalSelector, closeSelector }) => {
  document.querySelectorAll(closeSelector).forEach((button) => {
    button.addEventListener("click", () => {
      closeDialog(button.closest(modalSelector));
    });
  });
  document.querySelectorAll(modalSelector).forEach((modal) => {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeDialog(modal);
      }
    });
  });
};

export const clearFeedbackParams = (pageFeedback) => {
  if (!pageFeedback.message && !pageFeedback.error) return;
  const url = new URL(window.location.href);
  url.searchParams.delete("message");
  url.searchParams.delete("error");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
};
