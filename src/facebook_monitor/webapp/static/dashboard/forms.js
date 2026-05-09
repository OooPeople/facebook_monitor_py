import { editableSelector, showInlineStatus } from "./utils.js";
import {
  markSubmittedActionAnchor,
  markSubmittedConfigAnchor,
  saveScrollPosition,
  setFormDirty,
  suppressRefreshFor,
} from "./state.js";

export const setupConfigForms = (state) => {
  document.querySelectorAll(".config-form").forEach((form) => {
    const status = form.querySelector("[data-dirty-status]");
    const markDirty = () => {
      setFormDirty(state, form.dataset.targetId || form.id || "", true);
      showInlineStatus(status, "尚未儲存", "dirty");
    };
    form.querySelectorAll(editableSelector).forEach((node) => {
      node.addEventListener("input", markDirty);
      node.addEventListener("change", markDirty);
    });
  });
};

export const setupRefreshFields = () => {
  document.querySelectorAll("[data-refresh-form]").forEach((refreshForm) => {
    const firstRefreshInput = refreshForm.querySelector('input[name="refresh_mode"]');
    const form = refreshForm.closest("form") || firstRefreshInput?.form;
    if (!form) return;
    const refreshContainer = refreshForm.parentElement || form;
    const syncRefreshFields = () => {
      const mode = refreshContainer.querySelector('input[name="refresh_mode"]:checked')?.value
        || "fixed";
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
