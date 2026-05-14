import { setupCardCollapse } from "/static/dashboard/card_collapse.js";
import { setupDebugCopyButtons } from "/static/dashboard/debug_tools.js";
import { setupConfirmSubmitForms } from "/static/dashboard/dialogs.js";
import { setupConfigForms, setupFormSubmitTracking, setupRefreshFields } from "/static/dashboard/forms.js";
import { setupHitRecords } from "/static/dashboard/hit_records.js";
import { setupSettingsModals } from "/static/dashboard/modals.js";
import { setupRevisionClient } from "/static/dashboard/revision_client.js";
import { setupSidebar } from "/static/dashboard/sidebar.js";
import { setupSidebarLayout } from "/static/dashboard/sidebar_layout.js";
import {
  clearSubmittedActionAnchor,
  clearSubmittedConfigAnchor,
  createDashboardState,
  getSubmittedActionAnchor,
  getSubmittedConfigAnchor,
  restoreScrollPosition,
} from "/static/dashboard/state.js";
import { setupKeywordTabs, setupPreviewTabs } from "/static/dashboard/tabs.js";
import { setupThemeToggle } from "/static/dashboard/theme.js";
import { clearFeedbackParams, readJsonScript, showInlineStatus, showToast } from "/static/dashboard/utils.js";

const pageFeedback = readJsonScript("page-feedback", {});
const currentRevision = readJsonScript("dashboard-revision", "");
const state = createDashboardState(currentRevision);

if ("scrollRestoration" in window.history) {
  window.history.scrollRestoration = "manual";
}

const dispatchPageFeedback = () => {
  const message = pageFeedback.message || "";
  if (pageFeedback.error) {
    clearSubmittedActionAnchor();
  }
  const submittedConfigAnchor = getSubmittedConfigAnchor();
  const submittedActionAnchor = getSubmittedActionAnchor();
  const feedbackAnchor = message === "設定已更新"
    ? submittedConfigAnchor
    : submittedActionAnchor || window.location.hash.slice(1);
  const targetElement = feedbackAnchor ? document.getElementById(feedbackAnchor) : null;
  let handled = false;
  if (message === "設定已更新" && targetElement) {
    showInlineStatus(
      targetElement.querySelector("[data-dirty-status]"),
      "設定已更新",
      "saved",
      2500,
    );
    handled = true;
    clearSubmittedConfigAnchor();
  } else if (
    ["target 已開始", "target 已停止", "已排入掃描"].includes(message) &&
    targetElement
  ) {
    showInlineStatus(
      targetElement.querySelector("[data-action-status]"),
      message,
      "saved",
      2500,
    );
    handled = true;
    clearSubmittedActionAnchor();
  }
  if (message && !handled) {
    showToast(message, "success");
  }
  clearFeedbackParams(pageFeedback);
};

restoreScrollPosition();
setupThemeToggle();
setupConfigForms(state);
setupCardCollapse(state);
setupRefreshFields();
setupSidebar();
setupSidebarLayout({ showToast });
setupPreviewTabs();
setupKeywordTabs();
setupHitRecords({ showToast });
setupSettingsModals();
setupConfirmSubmitForms();
setupFormSubmitTracking();
setupRevisionClient(state);
setupDebugCopyButtons();
dispatchPageFeedback();
