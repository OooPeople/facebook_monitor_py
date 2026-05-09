import { isTargetCollapsed, isTargetDirty, setTargetCollapsed } from "./state.js";
import { showInlineStatus } from "./utils.js";

const collapseAnimationMs = 240;

const finishCollapseAnimation = (element) => {
  element.removeAttribute("data-collapse-animating");
  element.style.height = "";
  element.style.opacity = "";
};

const animateElementVisibility = (element, visible) => {
  if (!element) return;
  window.clearTimeout(Number(element.dataset.collapseAnimationTimer || "0"));
  element.removeAttribute("data-collapse-animating");

  if (visible) {
    element.hidden = false;
    element.style.height = "0px";
    element.style.opacity = "0";
    element.setAttribute("data-collapse-animating", "true");
    element.getBoundingClientRect();
    element.style.height = `${element.scrollHeight}px`;
    element.style.opacity = "1";
  } else {
    element.style.height = `${element.scrollHeight}px`;
    element.style.opacity = "1";
    element.setAttribute("data-collapse-animating", "true");
    element.getBoundingClientRect();
    element.style.height = "0px";
    element.style.opacity = "0";
  }

  const timer = window.setTimeout(() => {
    if (!visible) {
      element.hidden = true;
    }
    finishCollapseAnimation(element);
    delete element.dataset.collapseAnimationTimer;
  }, collapseAnimationMs);
  element.dataset.collapseAnimationTimer = String(timer);
};

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
