const buildStatusSummary = (baseStatus, modeLabel, detail) => (
  [baseStatus, modeLabel, detail].filter(Boolean).join(" · ")
);

const readStatusValue = (value, storedValue) => value ?? storedValue ?? "";

const readDefaultDetail = (value, storedValue, detail) => (
  value ?? storedValue ?? detail
);

const resolveSidebarStatusState = (
  node,
  status,
) => {
  const detail = readStatusValue(status.statusDetail, node.dataset.sidebarStatusDetail);
  return {
    base: readStatusValue(status.baseStatus, node.dataset.sidebarBaseStatus),
    detail,
    baseClass: readStatusValue(status.statusClass, node.dataset.sidebarStatusClass),
    defaultText: readDefaultDetail(
      status.defaultDetail,
      node.dataset.sidebarDefaultDetail,
      detail,
    ),
    mode: readStatusValue(status.modeLabel, node.dataset.sidebarModeLabel),
    modeCssClass: readStatusValue(status.modeClass, node.dataset.sidebarModeClass),
  };
};

const storeSidebarStatusState = (
  node,
  {
    base,
    detail,
    baseClass,
    defaultText,
    mode,
    modeCssClass,
  },
) => {
  node.dataset.sidebarBaseStatus = base;
  node.dataset.sidebarStatusClass = baseClass;
  node.dataset.sidebarStatusDetail = detail;
  node.dataset.sidebarDefaultDetail = defaultText;
  node.dataset.sidebarModeLabel = mode;
  node.dataset.sidebarModeClass = modeCssClass;
};

const appendStatusToken = (node, className, text) => {
  const token = document.createElement("span");
  token.className = className.trim();
  token.textContent = text;
  node.appendChild(token);
};

export const renderSidebarStatus = (
  node,
  status,
) => {
  if (!node) return;

  const state = resolveSidebarStatusState(node, status);

  storeSidebarStatusState(node, state);
  node.setAttribute("aria-label", buildStatusSummary(state.base, state.mode, state.detail));

  node.replaceChildren();
  appendStatusToken(
    node,
    `sidebar-status-token sidebar-status-pill ${state.baseClass}`,
    state.base,
  );

  if (state.mode) {
    appendStatusToken(
      node,
      `sidebar-status-token target-mode-chip sidebar-mode-chip ${state.modeCssClass}`,
      state.mode,
    );
  }

  if (state.detail) {
    appendStatusToken(node, "sidebar-status-detail", state.detail);
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
