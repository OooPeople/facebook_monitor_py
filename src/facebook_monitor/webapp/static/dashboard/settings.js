import {
  clearFeedbackParams,
  readJsonScript,
  setupDirtyFormStatus,
  showInlineStatus,
  showToast,
} from "/static/dashboard/utils.js";
import { setupConfirmSubmitForms } from "/static/dashboard/dialogs.js";
import { setupThemeToggle } from "/static/dashboard/theme.js";

const pageFeedback = readJsonScript("page-feedback", {});
const notificationForm = document.getElementById("notification-defaults-form");
const notificationStatus = document.querySelector(
  '[data-dirty-status="notification-defaults-form"]',
);
const targetKeywordForm = document.getElementById("target-keyword-defaults-form");
const targetKeywordStatus = document.querySelector(
  '[data-dirty-status="target-keyword-defaults-form"]',
);
const runtimeDiagnosticsCopyButton = document.querySelector(
  "[data-copy-runtime-diagnostics]",
);
const runtimeDiagnosticsStatus = document.querySelector(
  "[data-runtime-diagnostics-status]",
);

setupThemeToggle();
setupDirtyFormStatus({
  form: notificationForm,
  statusElement: notificationStatus,
});
setupDirtyFormStatus({
  form: targetKeywordForm,
  statusElement: targetKeywordStatus,
});
setupConfirmSubmitForms();

if (pageFeedback.message === "關鍵字預設值已保存") {
  showInlineStatus(targetKeywordStatus, "設定已更新", "saved", 2500);
} else if (pageFeedback.message === "通知預設值已保存") {
  showInlineStatus(notificationStatus, "設定已更新", "saved", 2500);
} else if (pageFeedback.message) {
  showToast(pageFeedback.message, "success");
}

clearFeedbackParams(pageFeedback);

runtimeDiagnosticsCopyButton?.addEventListener("click", async () => {
  const source = document.getElementById("runtime-diagnostics-copy-source");
  const text = source?.value || "";
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showInlineStatus(runtimeDiagnosticsStatus, "已複製", "saved", 1800);
  } catch (error) {
    showInlineStatus(runtimeDiagnosticsStatus, "無法複製", "dirty", 2500);
  }
});
