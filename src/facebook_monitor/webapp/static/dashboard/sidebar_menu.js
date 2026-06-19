import { sidebarRoot } from "/static/dashboard/sidebar_dom.js";

const SIDEBAR_MENU_ACTION_SELECTOR = ".sidebar-menu-action:not([hidden]):not(:disabled)";
let sidebarMenuPanelHost = null;
let sidebarMenuPanelNextSibling = null;

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

export const closeSidebarMenu = ({ restoreFocus = false } = {}) => {
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

export const setupSidebarMenuPosition = () => {
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
