import { setupDirtyFormStatus, showInlineStatus } from "/static/dashboard/utils.js";
import {
  markSubmittedActionAnchor,
  markSubmittedConfigAnchor,
  saveScrollPosition,
  setFormDirty,
  suppressRefreshFor,
} from "/static/dashboard/state.js";

export const setupConfigForms = (state) => {
  document.querySelectorAll(".config-form").forEach((form) => {
    const status = form.querySelector("[data-dirty-status]");
    const externalStatuses = form.id
      ? Array.from(document.querySelectorAll(`[data-dirty-status-for="${CSS.escape(form.id)}"]`))
      : [];
    setupDirtyFormStatus({
      form,
      statusElement: status,
      statusElements: externalStatuses,
      onDirtyChange: (dirty) => (
        setFormDirty(state, form.dataset.targetId || form.id || "", dirty)
      ),
    });
  });
};

export const setupRefreshFields = () => {
  document.querySelectorAll("[data-refresh-form]").forEach((refreshForm) => {
    const firstRefreshInput = refreshForm.querySelector('input[name="refresh_mode"]');
    const form = refreshForm.closest("form") || firstRefreshInput?.form;
    const refreshContainer = refreshForm.closest("[data-refresh-container]")
      || refreshForm.parentElement
      || form;
    if (!refreshContainer) return;
    const syncRefreshFields = () => {
      const mode = refreshContainer.querySelector('input[name="refresh_mode"]:checked')?.value
        || "floating";
      refreshContainer.querySelectorAll("[data-refresh-fixed]").forEach((node) => {
        node.hidden = mode === "floating";
      });
      refreshContainer.querySelectorAll("[data-refresh-floating]").forEach((node) => {
        node.hidden = mode !== "floating";
      });
    };
    refreshContainer.querySelectorAll('input[name="refresh_mode"]').forEach((node) => {
      node.addEventListener("change", syncRefreshFields);
    });
    syncRefreshFields();
  });
};

const notifyFieldChanged = (node) => {
  node?.dispatchEvent(new Event("input", { bubbles: true }));
  node?.dispatchEvent(new Event("change", { bubbles: true }));
};

export const setupSecretClearButtons = () => {
  document.querySelectorAll("[data-secret-field]").forEach((field) => {
    const input = field.querySelector("[data-secret-input]");
    const clearInput = field.querySelector("[data-secret-clear-input]");
    const button = field.querySelector("[data-secret-clear-button]");
    const status = field.parentElement?.querySelector("[data-secret-clear-status]");
    if (!input || !clearInput || !button) return;

    const defaultPlaceholder = input.dataset.secretDefaultPlaceholder || "";
    const clearPlaceholder = input.dataset.secretClearPlaceholder || "保存後將清除";
    const setClearState = (cleared) => {
      clearInput.value = cleared ? "on" : "";
      field.dataset.secretCleared = cleared ? "1" : "0";
      input.value = "";
      input.placeholder = cleared ? clearPlaceholder : defaultPlaceholder;
      button.textContent = cleared ? "取消" : "清除";
      if (status) {
        status.hidden = !cleared;
      }
      notifyFieldChanged(clearInput);
      notifyFieldChanged(input);
    };

    button.addEventListener("click", () => {
      setClearState(clearInput.value !== "on");
    });
    input.addEventListener("input", () => {
      if (clearInput.value === "on" && input.value.trim()) {
        setClearState(false);
      }
    });
  });
};

export const setupFormSubmitTracking = () => {
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      if (form.matches(".config-form")) {
        markSubmittedConfigAnchor(form.dataset.targetAnchor || "");
      } else if (form.dataset.actionAnchor) {
        markSubmittedActionAnchor(form.dataset.actionAnchor || "");
      }
      saveScrollPosition();
      suppressRefreshFor(5000);
    });
  });
};
