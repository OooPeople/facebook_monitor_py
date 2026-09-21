import { confirmDialog } from "/static/dashboard/dialogs.js";

const warningTitle = "Facebook 暫時限制存取警告";
const warningMessage = (
  "繼續執行可能無法取得內容，也可能遭到更久封鎖。是否仍要開始？"
);

let currentWarning = {
  active: false,
  generation: -1,
  warning_until: "",
};
let expiryRefresh = null;
let expiryTimerId = 0;

const normalizeWarning = (payload = {}) => ({
  active: Boolean(payload.active),
  generation: Number.isInteger(Number(payload.generation))
    ? Number(payload.generation)
    : -1,
  message: String(payload.message || ""),
  title: String(payload.title || ""),
  warning_until: String(payload.warning_until || ""),
});

const readWarningFromDom = () => {
  const banner = document.querySelector("[data-temporary-block-warning]");
  return normalizeWarning({
    active: Boolean(banner && !banner.hidden),
    generation: banner?.dataset.warningGeneration ?? -1,
    warning_until: banner?.dataset.warningUntil ?? "",
  });
};

const clearExpiryTimer = () => {
  if (!expiryTimerId) return;
  window.clearTimeout(expiryTimerId);
  expiryTimerId = 0;
};

const scheduleExpiryRefresh = () => {
  clearExpiryTimer();
  if (!currentWarning.active || !currentWarning.warning_until || !expiryRefresh) return;
  const expiresAt = Date.parse(currentWarning.warning_until);
  if (!Number.isFinite(expiresAt)) return;
  const delayMs = Math.max(0, Math.min(expiresAt - Date.now() + 50, 2147483647));
  expiryTimerId = window.setTimeout(() => {
    expiryTimerId = 0;
    void expiryRefresh?.();
  }, delayMs);
};

const syncStartForm = (form) => {
  const isStart = /\/start$/.test(new URL(form.action, window.location.href).pathname);
  const confirmationRequired = isStart && currentWarning.active;
  form.toggleAttribute("data-temporary-block-confirm-submit", confirmationRequired);
  const confirmedInput = form.querySelector("[data-temporary-block-warning-confirmed]");
  if (confirmedInput) confirmedInput.value = "0";
  const generationInput = form.querySelector("[data-temporary-block-warning-generation]");
  if (generationInput) generationInput.value = String(currentWarning.generation);
};

export const syncTemporaryBlockStartForms = () => {
  document.querySelectorAll("[data-monitoring-form]").forEach(syncStartForm);
};

export const applyTemporaryBlockWarningPayload = (payload) => {
  currentWarning = normalizeWarning(payload);
  const banner = document.querySelector("[data-temporary-block-warning]");
  if (banner) {
    banner.hidden = !currentWarning.active;
    banner.dataset.warningGeneration = String(currentWarning.generation);
    banner.dataset.warningUntil = currentWarning.warning_until;
    const title = banner.querySelector("[data-temporary-block-warning-title]");
    const message = banner.querySelector("[data-temporary-block-warning-message]");
    if (title) title.textContent = currentWarning.active ? currentWarning.title : "";
    if (message) message.textContent = currentWarning.active ? currentWarning.message : "";
  }
  syncTemporaryBlockStartForms();
  scheduleExpiryRefresh();
};

const confirmWarning = async (generation) => {
  const confirmed = await confirmDialog({
    title: warningTitle,
    message: warningMessage,
    confirmLabel: "了解風險，仍要開始",
    focusCancel: true,
  });
  return confirmed
    ? {
      temporary_block_warning_confirmed: true,
      warning_generation: Number(generation),
    }
    : null;
};

const confirmAndSubmitStartForm = async (form) => {
  while (form.isConnected && form.hasAttribute("data-temporary-block-confirm-submit")) {
    const actionAtPrompt = form.action;
    const generationInput = form.querySelector(
      "[data-temporary-block-warning-generation]",
    );
    const generationAtPrompt = generationInput?.value ?? "";
    const payload = await confirmWarning(generationAtPrompt);
    if (!payload || !form.isConnected) return;
    if (!form.hasAttribute("data-temporary-block-confirm-submit")) return;
    const currentAction = new URL(form.action, window.location.href).pathname;
    if (!/\/start$/.test(currentAction)) return;
    if (
      form.action !== actionAtPrompt
      || (generationInput?.value ?? "") !== generationAtPrompt
    ) continue;
    const confirmedInput = form.querySelector("[data-temporary-block-warning-confirmed]");
    if (confirmedInput) confirmedInput.value = "1";
    form.dataset.temporaryBlockConfirmedSubmit = "1";
    form.requestSubmit();
    return;
  }
};

export const confirmationPayloadForBatchStart = async (outcome = null) => {
  if (outcome?.confirmation_required) {
    return confirmWarning(outcome.warning_generation ?? -1);
  }
  if (currentWarning.active) {
    return confirmWarning(currentWarning.generation);
  }
  return {};
};

const setupStartForm = (form) => {
  form.addEventListener("submit", async (event) => {
    if (!form.hasAttribute("data-temporary-block-confirm-submit")) return;
    if (form.dataset.temporaryBlockConfirmedSubmit === "1") {
      delete form.dataset.temporaryBlockConfirmedSubmit;
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    if (form.dataset.temporaryBlockConfirmationInFlight === "1") return;
    form.dataset.temporaryBlockConfirmationInFlight = "1";
    try {
      await confirmAndSubmitStartForm(form);
    } finally {
      delete form.dataset.temporaryBlockConfirmationInFlight;
    }
  });
};

const repromptRedirectedTarget = () => {
  const locationUrl = new URL(window.location.href);
  const targetId = locationUrl.searchParams.get("temporary_block_reprompt_target");
  if (!targetId) return;
  locationUrl.searchParams.delete("temporary_block_reprompt_target");
  window.history.replaceState(
    {},
    "",
    `${locationUrl.pathname}${locationUrl.search}${locationUrl.hash}`,
  );
  const form = Array.from(document.querySelectorAll("[data-monitoring-form]")).find(
    (candidate) => candidate.dataset.targetId === targetId,
  );
  if (form?.hasAttribute("data-temporary-block-confirm-submit")) form.requestSubmit();
};

export const setupTemporaryBlockStartWarning = () => {
  currentWarning = readWarningFromDom();
  document.querySelectorAll("[data-monitoring-form]").forEach(setupStartForm);
  syncTemporaryBlockStartForms();
  repromptRedirectedTarget();
};

export const setupTemporaryBlockWarningExpiryRefresh = (refresh) => {
  expiryRefresh = refresh;
  scheduleExpiryRefresh();
  return () => {
    clearExpiryTimer();
    expiryRefresh = null;
  };
};
