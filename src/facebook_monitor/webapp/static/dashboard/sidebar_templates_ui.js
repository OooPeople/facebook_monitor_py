import { requestJson } from "/static/dashboard/api.js";
import { confirmDialog } from "/static/dashboard/dialogs.js";
import { saveScrollPosition } from "/static/dashboard/state.js";
import { listTargetIds } from "/static/dashboard/sidebar_dom.js";
import {
  bindDialogDismiss,
  formatClientErrorMessage,
} from "/static/dashboard/utils.js";

const reloadDashboardPreservingScroll = () => {
  saveScrollPosition();
  window.location.reload();
};

const collectTemplatePayload = (modal) => {
  const payload = {};
  modal.querySelectorAll("[data-sidebar-template-field]").forEach((field) => {
    const name = field.name || "";
    if (!name) return;
    const payloadName = field.dataset.sidebarTemplatePayloadName || name;
    if (field.type === "checkbox") {
      payload[payloadName] = Boolean(field.checked);
      return;
    }
    if (field.type === "radio") {
      if (field.checked) {
        payload[payloadName] = field.value;
      }
      return;
    }
    payload[payloadName] = field.value;
  });
  return payload;
};

const TEMPLATE_SECTION_LABELS = {
  keywords: "關鍵字",
  scan: "掃描",
  notifications: "通知",
  all: "全部",
};

const listGroupTargetNames = (group) => (
  Array.from(group?.querySelectorAll("[data-sidebar-item] .sidebar-name") || [])
    .map((node) => (node.textContent || "").trim())
    .filter(Boolean)
);

const buildTemplateApplyDetails = ({ section, targetNames, targetCount }) => {
  const previewNames = targetNames.slice(0, 5);
  const overflowCount = Math.max(targetCount - previewNames.length, 0);
  const targetSummary = previewNames.length
    ? `${previewNames.join("、")}${overflowCount ? `，以及另外 ${overflowCount} 個` : ""}`
    : "此群組目前沒有 target";
  return [
    `套用範圍：${TEMPLATE_SECTION_LABELS[section] || section}`,
    `影響 target：${targetSummary}`,
    section === "all"
      ? "套用前會先儲存目前整份群組模板；本次會覆蓋全部區段。"
      : "套用前會先儲存目前整份群組模板；本次只會覆蓋所選區段。",
    "會覆蓋這些 target 既有設定。",
    "不會影響群組外 target。",
    "此操作沒有自動復原。",
  ];
};

export const setupTemplateModals = (showToast) => {
  bindDialogDismiss({
    modalSelector: "[data-sidebar-template-modal]",
    closeSelector: "[data-sidebar-template-close]",
  });
  document.querySelectorAll("[data-sidebar-template-modal]").forEach((modal) => {
    modal.querySelector("[data-sidebar-template-save]")?.addEventListener("click", async () => {
      const groupId = modal.dataset.groupId || "";
      try {
        await requestJson(`/api/sidebar/groups/${encodeURIComponent(groupId)}/template`, {
          method: "PUT",
          payload: collectTemplatePayload(modal),
        });
        showToast?.("群組模板已儲存", "success");
        reloadDashboardPreservingScroll();
      } catch (error) {
        showToast?.(
          `群組模板儲存失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
          "error",
        );
      }
    });

    modal.querySelectorAll("[data-sidebar-template-apply]").forEach((button) => {
      button.addEventListener("click", async () => {
        const groupId = modal.dataset.groupId || "";
        const section = button.dataset.sidebarTemplateApply || "all";
        const group = document.querySelector(
          `[data-sidebar-group][data-group-id="${CSS.escape(groupId)}"]`,
        );
        const targetIds = listTargetIds(group?.querySelector("[data-sidebar-list]"));
        const count = targetIds.length;
        const targetNames = listGroupTargetNames(group);
        const confirmed = await confirmDialog({
          title: "套用群組模板",
          message: `即將套用到群組內 ${count} 個 target。`,
          details: buildTemplateApplyDetails({
            section,
            targetNames,
            targetCount: count,
          }),
          confirmLabel: "套用",
          danger: true,
        });
        if (!confirmed) return;
        try {
          await requestJson(`/api/sidebar/groups/${encodeURIComponent(groupId)}/template`, {
            method: "PUT",
            payload: collectTemplatePayload(modal),
          });
          const data = await requestJson(
            `/api/sidebar/groups/${encodeURIComponent(groupId)}/template/apply`,
            { payload: { sections: [section] } },
          );
          showToast?.(`已套用到 ${data.updated_count || 0} 個 target`, "success");
          reloadDashboardPreservingScroll();
        } catch (error) {
          showToast?.(
            `群組模板套用失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
            "error",
          );
        }
      });
    });
  });
};
