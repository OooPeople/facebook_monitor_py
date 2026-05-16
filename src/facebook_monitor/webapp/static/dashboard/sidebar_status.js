const buildStatusSummary = (baseStatus, modeLabel, detail) => (
  [baseStatus, modeLabel, detail].filter(Boolean).join(" · ")
);

export const renderSidebarStatus = (
  node,
  {
    baseStatus,
    statusClass,
    statusDetail,
    defaultDetail,
    modeLabel,
    modeClass,
  },
) => {
  if (!node) return;

  const base = baseStatus ?? node.dataset.sidebarBaseStatus ?? "";
  const detail = statusDetail ?? node.dataset.sidebarStatusDetail ?? "";
  const baseClass = statusClass ?? node.dataset.sidebarStatusClass ?? "";
  const defaultText = defaultDetail ?? node.dataset.sidebarDefaultDetail ?? detail;
  const mode = modeLabel ?? node.dataset.sidebarModeLabel ?? "";
  const modeCssClass = modeClass ?? node.dataset.sidebarModeClass ?? "";

  node.dataset.sidebarBaseStatus = base;
  node.dataset.sidebarStatusClass = baseClass;
  node.dataset.sidebarStatusDetail = detail;
  node.dataset.sidebarDefaultDetail = defaultText;
  node.dataset.sidebarModeLabel = mode;
  node.dataset.sidebarModeClass = modeCssClass;
  node.setAttribute("aria-label", buildStatusSummary(base, mode, detail));

  node.replaceChildren();

  const pill = document.createElement("span");
  pill.className = `sidebar-status-token sidebar-status-pill ${baseClass}`.trim();
  pill.textContent = base;
  node.appendChild(pill);

  if (mode) {
    const modeNode = document.createElement("span");
    modeNode.className = `sidebar-status-token target-mode-chip sidebar-mode-chip ${modeCssClass}`.trim();
    modeNode.textContent = mode;
    node.appendChild(modeNode);
  }

  if (detail) {
    const detailNode = document.createElement("span");
    detailNode.className = "sidebar-status-detail";
    detailNode.textContent = detail;
    node.appendChild(detailNode);
  }
};
