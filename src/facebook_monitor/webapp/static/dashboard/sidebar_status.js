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

const updateGroupMonitoringButton = (button, { active, empty }) => {
  if (!button) return;
  if (button.dataset.sidebarGroupMonitoringPending === "1") {
    button.disabled = true;
    return;
  }
  const action = active ? "stop" : "start";
  const label = active ? "停止群組" : "開始群組";
  button.dataset.sidebarGroupMonitoring = action;
  button.setAttribute("aria-label", label);
  button.title = label;
  button.disabled = empty;
  button.classList.toggle("is-active", active);
  button.querySelector(".sidebar-action-icon--play")?.toggleAttribute("hidden", active);
  button.querySelector(".sidebar-action-icon--stop")?.toggleAttribute("hidden", !active);
};

export const syncSidebarGroupMonitoringButtons = (root = document) => {
  const groups = [
    ...(root.matches?.("[data-sidebar-group][data-group-id]") ? [root] : []),
    ...root.querySelectorAll("[data-sidebar-group][data-group-id]"),
  ];
  groups.forEach((group) => {
    const items = Array.from(group.querySelectorAll("[data-sidebar-item]"));
    updateGroupMonitoringButton(group.querySelector("[data-sidebar-group-monitoring]"), {
      active: items.some((item) => item.dataset.sidebarItemActive === "1"),
      empty: items.length === 0,
    });
  });
};
