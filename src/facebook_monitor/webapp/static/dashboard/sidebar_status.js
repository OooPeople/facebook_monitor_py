const buildStatusSummary = (baseStatus, detail) => (
  detail ? `${baseStatus} · ${detail}` : baseStatus
);

export const renderSidebarStatus = (
  node,
  {
    baseStatus,
    statusClass,
    statusDetail,
    defaultDetail,
  },
) => {
  if (!node) return;

  const base = baseStatus ?? node.dataset.sidebarBaseStatus ?? "";
  const detail = statusDetail ?? node.dataset.sidebarStatusDetail ?? "";
  const baseClass = statusClass ?? node.dataset.sidebarStatusClass ?? "";
  const defaultText = defaultDetail ?? node.dataset.sidebarDefaultDetail ?? detail;

  node.dataset.sidebarBaseStatus = base;
  node.dataset.sidebarStatusClass = baseClass;
  node.dataset.sidebarStatusDetail = detail;
  node.dataset.sidebarDefaultDetail = defaultText;
  node.setAttribute("aria-label", buildStatusSummary(base, detail));

  node.replaceChildren();

  const pill = document.createElement("span");
  pill.className = `sidebar-status-pill ${baseClass}`.trim();
  pill.textContent = base;
  node.appendChild(pill);

  if (detail) {
    const detailNode = document.createElement("span");
    detailNode.className = "sidebar-status-detail";
    detailNode.textContent = detail;
    node.appendChild(detailNode);
  }
};
