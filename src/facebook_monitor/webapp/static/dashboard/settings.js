import {
  clearFeedbackParams,
  closeDialog,
  formatClientErrorMessage,
  openDialog,
  readJsonScript,
  setupDirtyFormStatus,
  setupScrollRestoration,
  showInlineStatus,
  showToast,
} from "/static/dashboard/utils.js";
import { csrfHeaders } from "/static/dashboard/csrf.js";
import { setupConfirmSubmitForms } from "/static/dashboard/dialogs.js";
import { setupThemeToggle } from "/static/dashboard/theme.js";

const pageFeedback = readJsonScript("page-feedback", {});
const targetKeywordForm = document.getElementById("target-keyword-defaults-form");
const targetKeywordStatus = document.querySelector(
  '[data-dirty-status="target-keyword-defaults-form"]',
);
const updateProgressModal = document.querySelector("[data-update-progress-modal]");
const updateProgressText = document.querySelector("[data-update-progress-text]");
const updateProgressHelp = document.querySelector("[data-update-progress-help]");
const updateProgressError = document.querySelector("[data-update-progress-error]");
const updateProgressFooter = document.querySelector("[data-update-progress-footer]");
const updateProgressClose = document.querySelector("[data-update-progress-close]");

setupThemeToggle();
setupConfirmSubmitForms();
setupScrollRestoration({
  storageKey: "facebook-monitor:settings-scroll",
  formSelector: "form:not([data-update-check-form]):not([data-update-install-form])",
});
setupDirtyFormStatus({
  form: targetKeywordForm,
  statusElement: targetKeywordStatus,
});

if (pageFeedback.feedback === "target_keyword_defaults_saved") {
  showInlineStatus(targetKeywordStatus, "預設值已儲存", "saved", 2500);
} else if (pageFeedback.message) {
  showToast(pageFeedback.message, "success");
}

clearFeedbackParams(pageFeedback);

const updateProgressMessages = [
  "正在下載新版",
  "正在驗證檔案",
  "正在準備更新",
];
const restartHelpText = "當自動跳出新頁面時，這個分頁就可以關閉。";
let updateWaitingDotsTimer = null;

const stopUpdateWaitingDots = () => {
  if (updateWaitingDotsTimer !== null) {
    window.clearInterval(updateWaitingDotsTimer);
    updateWaitingDotsTimer = null;
  }
};

const setUpdateModalState = ({ text, error = "", closeVisible = false }) => {
  stopUpdateWaitingDots();
  if (updateProgressText && text) {
    updateProgressText.textContent = text;
  }
  if (updateProgressHelp) {
    updateProgressHelp.hidden = true;
  }
  if (updateProgressError) {
    updateProgressError.textContent = error;
    updateProgressError.hidden = !error;
  }
  if (updateProgressClose) {
    updateProgressClose.hidden = !closeVisible;
  }
  if (updateProgressFooter) {
    updateProgressFooter.hidden = !closeVisible;
  }
};

const startUpdateWaitingDots = (baseText, { showRestartHelp = false } = {}) => {
  stopUpdateWaitingDots();
  if (updateProgressHelp) {
    updateProgressHelp.textContent = restartHelpText;
    updateProgressHelp.hidden = !showRestartHelp;
  }
  if (updateProgressError) {
    updateProgressError.textContent = "";
    updateProgressError.hidden = true;
  }
  if (updateProgressClose) {
    updateProgressClose.hidden = true;
  }
  if (updateProgressFooter) {
    updateProgressFooter.hidden = true;
  }
  let step = 1;
  const render = () => {
    if (!updateProgressText) return;
    const dots = ".".repeat(step);
    updateProgressText.textContent = `${baseText}${dots}`;
    step = (step + 1) % 4;
  };
  render();
  updateWaitingDotsTimer = window.setInterval(render, 650);
};

const readUpdateResponse = async (response) => {
  try {
    return await response.json();
  } catch (error) {
    return {};
  }
};

updateProgressModal?.addEventListener("cancel", (event) => {
  if (updateProgressModal.dataset.locked === "1") {
    event.preventDefault();
  }
});

updateProgressClose?.addEventListener("click", () => {
  closeDialog(updateProgressModal);
});

const replaceUpdateSectionFromHtml = (html) => {
  const nextDocument = new DOMParser().parseFromString(html, "text/html");
  const nextSection = nextDocument.querySelector("[data-update-section]");
  const currentSection = document.querySelector("[data-update-section]");
  if (!nextSection || !currentSection) {
    throw new Error("無法更新檢查結果。");
  }
  currentSection.replaceWith(document.importNode(nextSection, true));
};

const handleUpdateCheckSubmit = async (form) => {
  const button = form.querySelector('button[type="submit"]');
  const summary = document.querySelector("[data-update-summary]");
  button?.setAttribute("disabled", "disabled");
  if (summary) {
    summary.textContent = "正在檢查更新...";
  }
  try {
    const url = new URL(form.action, window.location.href);
    new FormData(form).forEach((value, key) => {
      url.searchParams.set(key, String(value));
    });
    const response = await fetch(url, {
      method: "GET",
      headers: { Accept: "text/html" },
    });
    if (!response.ok) {
      throw new Error(`檢查更新失敗：HTTP ${response.status}`);
    }
    replaceUpdateSectionFromHtml(await response.text());
  } catch (error) {
    button?.removeAttribute("disabled");
    if (summary) {
      summary.textContent = formatClientErrorMessage(error, "檢查更新失敗。");
    }
  }
};

const handleUpdateInstallSubmit = async (updateInstallForm) => {
  const submitButton = updateInstallForm.querySelector('button[type="submit"]');
  submitButton?.setAttribute("disabled", "disabled");
  if (updateProgressModal) {
    updateProgressModal.dataset.locked = "1";
    openDialog(updateProgressModal);
  }
  startUpdateWaitingDots(updateProgressMessages[0]);
  let messageIndex = 0;
  const messageTimer = window.setInterval(() => {
    messageIndex = Math.min(messageIndex + 1, updateProgressMessages.length - 1);
    startUpdateWaitingDots(updateProgressMessages[messageIndex]);
  }, 2200);
  try {
    const response = await fetch(updateInstallForm.action, {
      method: "POST",
      body: new FormData(updateInstallForm),
      headers: csrfHeaders({ Accept: "application/json" }),
    });
    const payload = await readUpdateResponse(response);
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || `更新失敗：HTTP ${response.status}`);
    }
    startUpdateWaitingDots("即將關閉並啟動新版", { showRestartHelp: true });
  } catch (error) {
    if (error instanceof TypeError) {
      startUpdateWaitingDots("正在啟動新版", { showRestartHelp: true });
    } else {
      submitButton?.removeAttribute("disabled");
      if (updateProgressModal) {
        delete updateProgressModal.dataset.locked;
      }
      setUpdateModalState({
        text: "更新沒有完成。",
        error: formatClientErrorMessage(error, "更新失敗，請稍後再試。"),
        closeVisible: true,
      });
    }
  } finally {
    window.clearInterval(messageTimer);
  }
};

document.addEventListener("submit", (event) => {
  const updateCheckForm = event.target.closest?.("[data-update-check-form]");
  if (updateCheckForm) {
    event.preventDefault();
    handleUpdateCheckSubmit(updateCheckForm);
    return;
  }
  const updateInstallForm = event.target.closest?.("[data-update-install-form]");
  if (updateInstallForm) {
    event.preventDefault();
    handleUpdateInstallSubmit(updateInstallForm);
  }
});
