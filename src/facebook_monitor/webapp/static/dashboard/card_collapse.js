import { isTargetCollapsed, isTargetDirty, setTargetCollapsed } from "/static/dashboard/state.js";
import { showInlineStatus } from "/static/dashboard/utils.js";
import { animateElementVisibility } from "/static/dashboard/collapse_animation.js";

const setCardCollapsed = (card, collapsed, { animate = false } = {}) => {
  const targetId = card.dataset.targetId || "";
  const collapsible = card.querySelector("[data-target-collapsible]");
  const summary = card.querySelector("[data-collapsed-summary]");
  const toggle = card.querySelector("[data-collapse-toggle]");

  card.classList.toggle("is-collapsed", collapsed);
  if (collapsible) {
    if (animate) {
      animateElementVisibility(collapsible, !collapsed);
    } else {
      collapsible.hidden = collapsed;
    }
  }
  if (summary) {
    if (animate) {
      animateElementVisibility(summary, collapsed);
    } else {
      summary.hidden = !collapsed;
    }
  }
  if (toggle) {
    const label = collapsed ? "展開 target" : "收合 target";
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  }
  setTargetCollapsed(targetId, collapsed);
};

const blockDirtyCollapse = (card) => {
  const status = card.querySelector("[data-dirty-status]");
  showInlineStatus(
    status,
    "此 target 有尚未儲存的變更，請先儲存後再收合。",
    "dirty",
    3500,
  );
};

export const setupCardCollapse = (state) => {
  document.querySelectorAll("[data-target-card]").forEach((card) => {
    const targetId = card.dataset.targetId || "";
    setCardCollapsed(card, isTargetCollapsed(targetId));

    const toggle = card.querySelector("[data-collapse-toggle]");
    if (!toggle) return;
    toggle.addEventListener("click", () => {
      const nextCollapsed = !card.classList.contains("is-collapsed");
      if (nextCollapsed && isTargetDirty(state, targetId)) {
        blockDirtyCollapse(card);
        return;
      }
      setCardCollapsed(card, nextCollapsed, { animate: true });
    });
  });
};
