import { syncSidebarGroupMonitoringButtons } from "/static/dashboard/sidebar_status.js";
import {
  closeExpandedGroupActions,
  setupGroupControls,
  setupGroupCreate,
  syncGroupCollapsedA11y,
} from "/static/dashboard/sidebar_groups_ui.js";
import {
  closeSidebarMenu,
  setupSidebarMenuPosition,
} from "/static/dashboard/sidebar_menu.js";
import {
  groupStack,
  listTargetIds,
  sidebarGroups,
  sidebarItems,
  sidebarLists,
  sidebarRoot,
} from "/static/dashboard/sidebar_dom.js";
import { setupSidebarSorting } from "/static/dashboard/sidebar_sorting.js";
import { setupTemplateModals } from "/static/dashboard/sidebar_templates_ui.js";
import { requestJson } from "/static/dashboard/api.js";

const updateEmptyStates = () => {
  sidebarLists().forEach((list) => {
    const empty = list.querySelector("[data-sidebar-empty]");
    if (!empty) return;
    empty.hidden = listTargetIds(list).length > 0;
  });
  syncSidebarGroupMonitoringButtons();
};

const reorderCardsBySidebar = () => {
  const targetList = document.querySelector(".target-list");
  if (!targetList) return;
  sidebarItems().forEach((item) => {
    const card = document.querySelector(
      `[data-target-card][data-target-id="${CSS.escape(item.dataset.targetId || "")}"]`,
    );
    if (card) {
      targetList.appendChild(card);
    }
  });
};

export const setupSidebarLayout = ({ showToast } = {}) => {
  if (!sidebarRoot()) return;
  setupSidebarMenuPosition();
  setupSidebarSorting({
    showToast,
    requestJson,
    sidebarRoot,
    groupStack,
    sidebarGroups,
    sidebarLists,
    updateEmptyStates,
    reorderCardsBySidebar,
    closeExpandedGroupActions,
    closeSidebarMenu,
  });
  setupGroupControls(showToast);
  setupGroupCreate({ showToast, closeSidebarMenu });
  setupTemplateModals(showToast);
  updateEmptyStates();
  syncGroupCollapsedA11y();
};
