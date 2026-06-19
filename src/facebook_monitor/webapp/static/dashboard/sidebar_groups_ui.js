import { requestJson } from "/static/dashboard/api.js";
import { confirmDialog, promptDialog } from "/static/dashboard/dialogs.js";
import { syncSidebarGroupMonitoringButtons } from "/static/dashboard/sidebar_status.js";
import { saveScrollPosition } from "/static/dashboard/state.js";
import {
  prefersReducedMotion,
  sidebarGroups,
  sidebarRoot,
} from "/static/dashboard/sidebar_dom.js";
import {
  formatClientErrorMessage,
  openDialog,
} from "/static/dashboard/utils.js";

const isSorting = () => sidebarRoot()?.classList.contains("sorting");

const reloadDashboardPreservingScroll = () => {
  saveScrollPosition();
  window.location.reload();
};

export const syncGroupCollapsedA11y = () => {
  sidebarGroups().forEach((group) => {
    const list = group.querySelector("[data-sidebar-list]");
    if (!list) return;
    const collapsed = group.classList.contains("collapsed");
    list.setAttribute("aria-hidden", String(collapsed));
    list.inert = collapsed;
  });
};

export const closeExpandedGroupActions = (except = null) => {
  document.querySelectorAll("[data-sidebar-group-actions].expanded").forEach((actions) => {
    if (actions === except) return;
    actions.classList.remove("expanded");
    actions.querySelector("[data-sidebar-group-actions-toggle]")?.setAttribute(
      "aria-expanded",
      "false",
    );
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

export const setupGroupControls = (showToast) => {
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

export const setupGroupCreate = ({ showToast, closeSidebarMenu } = {}) => {
  document.querySelectorAll("[data-sidebar-create-group]").forEach((button) => {
    button.addEventListener("click", async () => {
      closeSidebarMenu?.();
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
