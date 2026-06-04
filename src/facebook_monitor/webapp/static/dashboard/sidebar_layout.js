import { requestJson } from "/static/dashboard/api.js";
import { confirmDialog, promptDialog } from "/static/dashboard/dialogs.js";
import { syncSidebarGroupMonitoringButtons } from "/static/dashboard/sidebar_status.js";
import { saveScrollPosition } from "/static/dashboard/state.js";
import {
  groupStack,
  listTargetIds,
  prefersReducedMotion,
  sidebarGroups,
  sidebarItems,
  sidebarLists,
  sidebarRoot,
} from "/static/dashboard/sidebar_dom.js";
import { setupSidebarSorting } from "/static/dashboard/sidebar_sorting.js";
import {
  bindDialogDismiss,
  formatClientErrorMessage,
  openDialog,
} from "/static/dashboard/utils.js";

const isSorting = () => sidebarRoot()?.classList.contains("sorting");
const SIDEBAR_MENU_ACTION_SELECTOR = ".sidebar-menu-action:not([hidden]):not(:disabled)";
let sidebarMenuPanelHost = null;
let sidebarMenuPanelNextSibling = null;

const reloadDashboardPreservingScroll = () => {
  saveScrollPosition();
  window.location.reload();
};

const updateEmptyStates = () => {
  sidebarLists().forEach((list) => {
    const empty = list.querySelector("[data-sidebar-empty]");
    if (!empty) return;
    empty.hidden = listTargetIds(list).length > 0;
  });
  syncSidebarGroupMonitoringButtons();
};

const syncGroupCollapsedA11y = () => {
  sidebarGroups().forEach((group) => {
    const list = group.querySelector("[data-sidebar-list]");
    if (!list) return;
    const collapsed = group.classList.contains("collapsed");
    list.setAttribute("aria-hidden", String(collapsed));
    list.inert = collapsed;
  });
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

const closeExpandedGroupActions = (except = null) => {
  document.querySelectorAll("[data-sidebar-group-actions].expanded").forEach((actions) => {
    if (actions === except) return;
    actions.classList.remove("expanded");
    actions.querySelector("[data-sidebar-group-actions-toggle]")?.setAttribute(
      "aria-expanded",
      "false",
    );
  });
};

const sidebarMenuPanel = (menu) => (
  menu?.querySelector(".sidebar-menu-panel")
  || document.querySelector(".sidebar-menu-panel[data-sidebar-menu-floating]")
);

const floatSidebarMenuPanel = (menu, panel) => {
  if (panel.parentElement === document.body) return;
  sidebarMenuPanelHost = menu;
  sidebarMenuPanelNextSibling = panel.nextSibling;
  panel.dataset.sidebarMenuFloating = "1";
  document.body.appendChild(panel);
};

const restoreSidebarMenuPanel = (menu, panel = sidebarMenuPanel(menu)) => {
  if (!panel?.dataset.sidebarMenuFloating) return;
  const host = sidebarMenuPanelHost || menu;
  if (host?.isConnected) {
    host.insertBefore(
      panel,
      sidebarMenuPanelNextSibling?.parentNode === host ? sidebarMenuPanelNextSibling : null,
    );
  }
  delete panel.dataset.sidebarMenuFloating;
  sidebarMenuPanelHost = null;
  sidebarMenuPanelNextSibling = null;
};

const focusFirstSidebarMenuAction = (panel) => {
  panel?.querySelector(SIDEBAR_MENU_ACTION_SELECTOR)?.focus?.({ preventScroll: true });
};

const focusSidebarMenuTrigger = (menu) => {
  menu?.querySelector(".sidebar-menu-trigger")?.focus?.({ preventScroll: true });
};

const positionSidebarMenuPanel = ({ focusFirstAction = false } = {}) => {
  const menu = document.querySelector("[data-sidebar-menu]");
  const panel = sidebarMenuPanel(menu);
  const trigger = menu?.querySelector(".sidebar-menu-trigger");
  if (!menu?.open || !panel || !trigger) return;
  const gap = 10;
  const viewportPadding = 8;
  floatSidebarMenuPanel(menu, panel);
  const rect = trigger.getBoundingClientRect();
  const panelWidth = panel.offsetWidth || 132;
  const left = Math.min(
    rect.right + gap,
    window.innerWidth - panelWidth - viewportPadding,
  );
  panel.style.setProperty("--sidebar-menu-left", `${Math.max(viewportPadding, left)}px`);
  panel.style.setProperty("--sidebar-menu-top", `${Math.max(viewportPadding, rect.top)}px`);
  if (focusFirstAction) {
    focusFirstSidebarMenuAction(panel);
  }
};

const closeSidebarMenu = ({ restoreFocus = false } = {}) => {
  const menu = document.querySelector("[data-sidebar-menu]");
  const trigger = menu?.querySelector(".sidebar-menu-trigger");
  if (!menu) return;
  menu.open = false;
  restoreSidebarMenuPanel(menu);
  trigger?.setAttribute("aria-expanded", "false");
  if (restoreFocus) {
    focusSidebarMenuTrigger(menu);
  }
};

const setSidebarMenuOpen = (open) => {
  const menu = document.querySelector("[data-sidebar-menu]");
  const trigger = menu?.querySelector(".sidebar-menu-trigger");
  if (!menu) return;
  menu.open = open;
  trigger?.setAttribute("aria-expanded", String(open));
  if (open) {
    window.requestAnimationFrame(() => {
      positionSidebarMenuPanel({ focusFirstAction: true });
    });
  } else {
    restoreSidebarMenuPanel(menu);
  }
};

const setupSidebarMenuPosition = () => {
  const menu = document.querySelector("[data-sidebar-menu]");
  const trigger = menu?.querySelector(".sidebar-menu-trigger");
  if (!menu || !trigger) return;
  trigger.setAttribute("aria-expanded", String(Boolean(menu.open)));
  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setSidebarMenuOpen(!menu.open);
  });
  menu.addEventListener("toggle", () => {
    if (menu.open) {
      positionSidebarMenuPanel();
    } else {
      restoreSidebarMenuPanel(menu);
    }
  });
  window.addEventListener("resize", positionSidebarMenuPanel);
  sidebarRoot()?.addEventListener("scroll", positionSidebarMenuPanel, { passive: true });
  document.addEventListener("click", (event) => {
    if (
      !menu.open
      || event.target.closest?.("[data-sidebar-menu]")
      || event.target.closest?.(".sidebar-menu-panel")
    ) return;
    closeSidebarMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSidebarMenu({ restoreFocus: true });
    }
  });
};

const setupGroupControls = (showToast) => {
  document.querySelectorAll("[data-sidebar-group-collapse]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (isSorting()) return;
      const group = button.closest("[data-sidebar-group]");
      if (!group) return;
      const collapsed = !group.classList.contains("collapsed");
      animateGroupCollapsed(group, collapsed);
      const groupId = group.dataset.groupId || "";
      if (!groupId) return;
      try {
        await requestJson(`/api/sidebar/groups/${encodeURIComponent(groupId)}`, {
          method: "PATCH",
          payload: { collapsed },
        });
      } catch (error) {
        showToast?.(
          `群組狀態更新失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
          "error",
        );
      }
    });
  });

  setupGroupActionToggles();

  document.querySelectorAll("[data-sidebar-group-rename]").forEach((button) => {
    button.addEventListener("click", async () => {
      const group = button.closest("[data-sidebar-group]");
      const groupId = group?.dataset.groupId || "";
      const currentName = group?.querySelector(".sidebar-group-name")?.textContent || "";
      const name = await promptDialog({
        title: "重新命名群組",
        label: "群組名稱",
        value: currentName,
        confirmLabel: "儲存",
      });
      if (!groupId || name === null) return;
      try {
        await requestJson(`/api/sidebar/groups/${encodeURIComponent(groupId)}`, {
          method: "PATCH",
          payload: { name },
        });
        reloadDashboardPreservingScroll();
      } catch (error) {
        showToast?.(
          `群組更名失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
          "error",
        );
      }
    });
  });

  document.querySelectorAll("[data-sidebar-group-delete]").forEach((button) => {
    button.addEventListener("click", async () => {
      const group = button.closest("[data-sidebar-group]");
      const groupId = group?.dataset.groupId || "";
      const name = group?.querySelector(".sidebar-group-name")?.textContent || "";
      const confirmed = await confirmDialog({
        title: "刪除群組",
        message: `刪除空群組「${name}」？群組內若仍有 target，系統會拒絕刪除。`,
        confirmLabel: "刪除",
        danger: true,
      });
      if (!groupId || !confirmed) return;
      try {
        await requestJson(`/api/sidebar/groups/${encodeURIComponent(groupId)}`, {
          method: "DELETE",
        });
        reloadDashboardPreservingScroll();
      } catch (error) {
        showToast?.(
          `群組刪除失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
          "error",
        );
      }
    });
  });

  document.querySelectorAll("[data-sidebar-group-settings]").forEach((button) => {
    button.addEventListener("click", () => {
      const group = button.closest("[data-sidebar-group]");
      const groupId = group?.dataset.groupId || "";
      openDialog(
        document.querySelector(
          `[data-sidebar-template-modal][data-group-id="${CSS.escape(groupId)}"]`,
        ),
      );
    });
  });

  document.querySelectorAll("[data-sidebar-group-monitoring]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (isSorting() || button.disabled) return;
      const group = button.closest("[data-sidebar-group]");
      const groupId = group?.dataset.groupId || "";
      const action = button.dataset.sidebarGroupMonitoring || "start";
      if (!groupId) return;
      button.dataset.sidebarGroupMonitoringPending = "1";
      button.disabled = true;
      try {
        const data = await requestJson(
          `/api/sidebar/groups/${encodeURIComponent(groupId)}/${encodeURIComponent(action)}`,
        );
        showToast?.(data.message || "群組狀態已更新", "success");
        reloadDashboardPreservingScroll();
      } catch (error) {
        delete button.dataset.sidebarGroupMonitoringPending;
        if (group) {
          syncSidebarGroupMonitoringButtons(group);
        } else {
          button.disabled = false;
        }
        showToast?.(
          `群組狀態更新失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
          "error",
        );
      }
    });
  });
};

const animateGroupCollapsed = (group, collapsed) => {
  const list = group.querySelector("[data-sidebar-list]");
  if (!list || prefersReducedMotion()) {
    group.classList.toggle("collapsed", collapsed);
    syncGroupCollapsedA11y();
    return;
  }
  list.inert = collapsed;
  list.setAttribute("aria-hidden", String(collapsed));
  list.dataset.sidebarCollapseAnimating = "1";
  if (collapsed) {
    list.style.maxHeight = `${list.scrollHeight}px`;
    list.style.opacity = "1";
    list.offsetHeight;
    group.classList.add("collapsed");
    list.style.maxHeight = "0px";
    list.style.opacity = "0";
  } else {
    group.classList.remove("collapsed");
    list.style.maxHeight = "0px";
    list.style.opacity = "0";
    list.offsetHeight;
    list.style.maxHeight = `${list.scrollHeight}px`;
    list.style.opacity = "1";
  }
  window.setTimeout(() => {
    if (!document.body.contains(list)) return;
    delete list.dataset.sidebarCollapseAnimating;
    list.style.maxHeight = "";
    list.style.opacity = "";
  }, 260);
};

const setupGroupActionToggles = () => {
  document.querySelectorAll("[data-sidebar-group-actions-toggle]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const actions = button.closest("[data-sidebar-group-actions]");
      if (!actions) return;
      const expanded = !actions.classList.contains("expanded");
      closeExpandedGroupActions(actions);
      actions.classList.toggle("expanded", expanded);
      button.setAttribute("aria-expanded", String(expanded));
    });
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-sidebar-group-actions]")) return;
    closeExpandedGroupActions();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeExpandedGroupActions();
    }
  });
};

const setupGroupCreate = (showToast) => {
  document.querySelectorAll("[data-sidebar-create-group]").forEach((button) => {
    button.addEventListener("click", async () => {
      closeSidebarMenu();
      const name = await promptDialog({
        title: "新增群組",
        label: "群組名稱",
        confirmLabel: "建立",
      });
      if (name === null) return;
      try {
        await requestJson("/api/sidebar/groups", { payload: { name } });
        reloadDashboardPreservingScroll();
      } catch (error) {
        showToast?.(
          `群組建立失敗：${formatClientErrorMessage(error, "請稍後再試")}`,
          "error",
        );
      }
    });
  });
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

const setupTemplateModals = (showToast) => {
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
  setupGroupCreate(showToast);
  setupTemplateModals(showToast);
  updateEmptyStates();
  syncGroupCollapsedA11y();
};
