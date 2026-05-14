import { closeDialog } from "/static/dashboard/utils.js";

const SORTABLE_ANIMATION_MS = 160;
const SORTABLE_SWAP_THRESHOLD = 0.75;
const SIDEBAR_TARGET_GROUP = "sidebar-targets";
const SORTABLE_MODULE_PATH = "/static/vendor/sortablejs/sortable.esm.js";

let sortableConstructor = null;

const loadSortable = async () => {
  if (sortableConstructor) {
    return sortableConstructor;
  }
  const module = await import(SORTABLE_MODULE_PATH);
  sortableConstructor = module.default;
  return sortableConstructor;
};

const prefersReducedMotion = () => (
  window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches
);

const sortableAnimation = () => (prefersReducedMotion() ? 0 : SORTABLE_ANIMATION_MS);

const listTargetIds = (list) => (
  Array.from((list || document.createElement("div")).querySelectorAll("[data-sidebar-item]"))
    .map((item) => item.dataset.targetId || "")
    .filter(Boolean)
);

const collectPlacements = (sidebarLists) => ({
  groups: sidebarLists().map((list) => ({
    group_id: list.dataset.groupId || null,
    target_ids: listTargetIds(list),
  })),
});

const collectGroupOrder = (sidebarGroups) => ({
  group_ids: sidebarGroups()
    .map((group) => group.dataset.groupId || "")
    .filter(Boolean),
});

const snapshotLayout = ({ sidebarGroups, sidebarLists }) => JSON.stringify({
  groups: sidebarGroups().map((group) => group.dataset.groupId || ""),
  placements: collectPlacements(sidebarLists).groups,
});

const keepSystemGroupLast = (groupStack, sidebarGroups) => {
  const stack = groupStack();
  if (!stack) return;
  const systemGroup = sidebarGroups().find((group) => !group.dataset.groupId);
  if (systemGroup && systemGroup.parentElement === stack) {
    stack.appendChild(systemGroup);
  }
};

const buildSortableOptions = (extraOptions = {}) => ({
  animation: sortableAnimation(),
  chosenClass: "sidebar-sort-chosen",
  dragClass: "sidebar-sort-drag",
  direction: "vertical",
  fallbackClass: "sidebar-sort-fallback",
  fallbackOnBody: true,
  forceFallback: true,
  ghostClass: "sidebar-sort-ghost",
  swapThreshold: SORTABLE_SWAP_THRESHOLD,
  ...extraOptions,
});

export const setupSidebarSorting = ({
  showToast,
  requestJson,
  sidebarRoot,
  groupStack,
  sidebarGroups,
  sidebarLists,
  updateEmptyStates,
  reorderCardsBySidebar,
  closeExpandedGroupActions,
}) => {
  let targetSortables = [];
  let groupSortable = null;
  let sortableSetupPromise = null;

  const setupSortables = async () => {
    if (sortableSetupPromise) {
      return sortableSetupPromise;
    }
    sortableSetupPromise = (async () => {
      const Sortable = await loadSortable();
      targetSortables = sidebarLists().map((list) => new Sortable(list, buildSortableOptions({
        disabled: true,
        draggable: "[data-sidebar-item]",
        emptyInsertThreshold: 12,
        group: SIDEBAR_TARGET_GROUP,
        handle: "[data-sidebar-drag-handle]",
        onSort: () => {
          updateEmptyStates();
        },
      })));

      groupSortable = groupStack()
        ? new Sortable(groupStack(), buildSortableOptions({
          disabled: true,
          draggable: "[data-sidebar-group][data-group-id]",
          handle: "[data-sidebar-group-drag-handle]",
          onEnd: () => {
            keepSystemGroupLast(groupStack, sidebarGroups);
          },
          onMove: (event) => Boolean(event.related?.dataset.groupId),
        }))
        : null;
    })();
    try {
      await sortableSetupPromise;
    } catch (error) {
      sortableSetupPromise = null;
      throw error;
    }
    return sortableSetupPromise;
  };

  const allSortables = () => [
    ...targetSortables,
    ...(groupSortable ? [groupSortable] : []),
  ];

  const setSortMode = (enabled) => {
    const root = sidebarRoot();
    if (!root) return;
    closeExpandedGroupActions();
    root.classList.toggle("sorting", enabled);
    root.dataset.sortSnapshot = enabled ? snapshotLayout({ sidebarGroups, sidebarLists }) : "";
    document.querySelector("[data-sidebar-confirm-sort]").hidden = !enabled;
    document.querySelector("[data-sidebar-start-sort]").hidden = enabled;
    document.querySelector("[data-sidebar-cancel-sort]").hidden = !enabled;
    document.querySelectorAll("[data-sidebar-group-drag-handle]").forEach((handle) => {
      handle.setAttribute("aria-label", enabled ? "拖曳群組排序" : "收合群組");
    });
    allSortables().forEach((sortable) => {
      sortable.option("animation", sortableAnimation());
      sortable.option("disabled", !enabled);
    });
  };

  const saveLayout = async () => {
    const groupOrder = collectGroupOrder(sidebarGroups);
    const placements = collectPlacements(sidebarLists);
    const data = await requestJson("/api/sidebar/layout", {
      payload: {
        group_ids: groupOrder.group_ids,
        groups: placements.groups,
      },
    });
    updateEmptyStates();
    reorderCardsBySidebar();
    setSortMode(false);
    showToast?.("排序已更新", "success");
    return data;
  };

  document.querySelector("[data-sidebar-start-sort]")?.addEventListener("click", async () => {
    closeDialog(document.querySelector("[data-sidebar-menu]"));
    try {
      await setupSortables();
      setSortMode(true);
    } catch (error) {
      showToast?.(`排序功能載入失敗: ${error.message}`, "error");
    }
  });
  document.querySelector("[data-sidebar-cancel-sort]")?.addEventListener("click", () => {
    window.location.reload();
  });
  document.querySelector("[data-sidebar-confirm-sort]")?.addEventListener("click", async () => {
    const before = sidebarRoot()?.dataset.sortSnapshot || "";
    const after = snapshotLayout({ sidebarGroups, sidebarLists });
    if (before === after) {
      setSortMode(false);
      return;
    }
    try {
      await saveLayout();
    } catch (error) {
      showToast?.(`排序更新失敗: ${error.message}`, "error");
      window.location.reload();
    }
  });
};
