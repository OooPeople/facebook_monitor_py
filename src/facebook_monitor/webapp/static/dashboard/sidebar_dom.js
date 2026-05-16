export const sidebarRoot = () => document.querySelector("[data-sidebar-layout]");

export const groupStack = () => document.querySelector("[data-sidebar-group-stack]");

export const sidebarGroups = () => Array.from(document.querySelectorAll("[data-sidebar-group]"));

export const sidebarLists = () => Array.from(document.querySelectorAll("[data-sidebar-list]"));

export const sidebarItems = () => Array.from(document.querySelectorAll("[data-sidebar-item]"));

export const getSidebarLinks = () => (
  Array.from(document.querySelectorAll("[data-sidebar-target]"))
);

export const getTargetCards = () => (
  Array.from(document.querySelectorAll("[data-target-card][id]"))
);

export const listTargetIds = (list) => (
  Array.from((list || document.createElement("div")).querySelectorAll("[data-sidebar-item]"))
    .map((item) => item.dataset.targetId || "")
    .filter(Boolean)
);

export const prefersReducedMotion = () => (
  window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches
);
