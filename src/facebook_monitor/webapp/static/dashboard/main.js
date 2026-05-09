import { setupCardCollapse } from "./card_collapse.js";
import { setupDebugCopyButtons } from "./debug_tools.js";
import { setupConfigForms, setupFormSubmitTracking, setupRefreshFields } from "./forms.js";
import { setupHitRecords } from "./hit_records.js?v=ui-refactor-phase18-form-sidebar-status";
import { setupSettingsModals } from "./modals.js";
import { setupRevisionClient } from "./revision_client.js?v=ui-refactor-phase18-form-sidebar-status";
import { setupSidebar } from "./sidebar.js?v=ui-refactor-phase18-form-sidebar-status";
import {
  clearSubmittedActionAnchor,
  clearSubmittedConfigAnchor,
  createDashboardState,
  getSubmittedActionAnchor,
  getSubmittedConfigAnchor,
  restoreScrollPosition,
} from "./state.js";
import { setupPreviewTabs } from "./tabs.js";
import { clearFeedbackParams, readJsonScript, showInlineStatus, showToast } from "./utils.js";

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
setupConfigForms(state);
setupCardCollapse(state);
setupRefreshFields();
setupSidebar();
setupPreviewTabs();
setupHitRecords({ showToast });
setupSettingsModals();
setupFormSubmitTracking();
setupRevisionClient(state);
setupDebugCopyButtons();
dispatchPageFeedback();
