import { getActivePreviewTab, setActivePreviewTab } from "/static/dashboard/state.js";

export const activatePreviewTab = (tabsRoot, tabName) => {
  tabsRoot.querySelectorAll("[data-preview-tab]").forEach((tab) => {
    const active = tab.dataset.previewTab === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  tabsRoot.querySelectorAll("[data-preview-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.previewPanel !== tabName;
  });
};

export const setupPreviewTabs = () => {
  document.querySelectorAll("[data-preview-tabs]").forEach((tabsRoot) => {
    const targetId = tabsRoot.dataset.targetId || "";
    activatePreviewTab(tabsRoot, getActivePreviewTab(targetId));
    tabsRoot.querySelectorAll("[data-preview-tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        const tabName = tab.dataset.previewTab || "latest";
        setActivePreviewTab(targetId, tabName);
        activatePreviewTab(tabsRoot, tabName);
      });
    });
  });
};

export const activateKeywordTab = (tabsRoot, tabName) => {
  tabsRoot.querySelectorAll("[data-keyword-tab]").forEach((tab) => {
    const active = tab.dataset.keywordTab === tabName;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  tabsRoot.querySelectorAll("[data-keyword-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.keywordPanel !== tabName;
  });
};

export const setupKeywordTabs = () => {
  document.querySelectorAll("[data-keyword-tabs]").forEach((tabsRoot) => {
    activateKeywordTab(tabsRoot, "exclude");
    tabsRoot.querySelectorAll("[data-keyword-tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        activateKeywordTab(tabsRoot, tab.dataset.keywordTab || "exclude");
      });
    });
  });
};
