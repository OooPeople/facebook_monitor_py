import { editableSelector } from "/static/dashboard/utils.js";

export const storageKeys = {
  scroll: "facebookMonitor.dashboard.scrollY",
  scrollSavedAt: "facebookMonitor.dashboard.scrollSavedAt",
  suppressUntil: "facebookMonitor.dashboard.suppressReloadUntil",
  submittedConfigAnchor: "facebookMonitor.dashboard.submittedConfigAnchor",
  submittedActionAnchor: "facebookMonitor.dashboard.submittedActionAnchor",
  collapsedTargets: "facebookMonitor.dashboard.collapsedTargets",
  activePreviewTabs: "facebookMonitor.dashboard.activePreviewTabs",
};

export const createDashboardState = (currentRevision = "") => ({
  currentRevision,
  dirtyTargets: new Set(),
  pendingRefresh: false,
  revisionTransport: "initializing",
  sseConnected: false,
  lastSseEventAt: 0,
  partialUpdateSeq: 0,
  partialUpdateInFlight: false,
  pendingTargetUpdates: new Set(),
});

export const isFormDirty = (state) => state.dirtyTargets.size > 0;

export const isTargetDirty = (state, targetId) => (
  Boolean(targetId) && state.dirtyTargets.has(targetId)
);

export const setFormDirty = (state, targetId, dirty) => {
  if (!targetId) return;
  if (dirty) {
    state.dirtyTargets.add(targetId);
  } else {
    state.dirtyTargets.delete(targetId);
  }
};

export const markTargetUpdatePending = (state, targetId) => {
  if (targetId) {
    state.pendingTargetUpdates.add(targetId);
  }
};

export const clearTargetUpdatePending = (state, targetId) => {
  if (targetId) {
    state.pendingTargetUpdates.delete(targetId);
  }
};

export const hasPendingTargetUpdate = (state, targetId) => (
  Boolean(targetId) && state.pendingTargetUpdates.has(targetId)
);

const readStoredObject = (key) => {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (error) {
    return {};
  }
};

const writeStoredObject = (key, value) => {
  localStorage.setItem(key, JSON.stringify(value));
};

export const isTargetCollapsed = (targetId) => {
  const collapsedTargets = readStoredObject(storageKeys.collapsedTargets);
  return Boolean(collapsedTargets[targetId]);
};

export const setTargetCollapsed = (targetId, collapsed) => {
  if (!targetId) return;
  const collapsedTargets = readStoredObject(storageKeys.collapsedTargets);
  if (collapsed) {
    collapsedTargets[targetId] = true;
  } else {
    delete collapsedTargets[targetId];
  }
  writeStoredObject(storageKeys.collapsedTargets, collapsedTargets);
};

export const getActivePreviewTab = (targetId) => {
  const activeTabs = readStoredObject(storageKeys.activePreviewTabs);
  return activeTabs[targetId] || "latest";
};

export const setActivePreviewTab = (targetId, tabName) => {
  if (!targetId || !tabName) return;
  const activeTabs = readStoredObject(storageKeys.activePreviewTabs);
  activeTabs[targetId] = tabName;
  writeStoredObject(storageKeys.activePreviewTabs, activeTabs);
};

export const saveScrollPosition = () => {
  sessionStorage.setItem(storageKeys.scroll, String(window.scrollY));
  sessionStorage.setItem(storageKeys.scrollSavedAt, String(Date.now()));
};

export const restoreScrollPosition = () => {
  const savedScrollY = Number(sessionStorage.getItem(storageKeys.scroll) || "");
  const savedAt = Number(sessionStorage.getItem(storageKeys.scrollSavedAt) || "");
  if (Number.isFinite(savedScrollY) && savedAt && Date.now() - savedAt < 10000) {
    window.requestAnimationFrame(() => window.scrollTo(0, savedScrollY));
  }
};

export const suppressRefreshFor = (milliseconds) => {
  sessionStorage.setItem(storageKeys.suppressUntil, String(Date.now() + milliseconds));
};

export const shouldDelayRefresh = (state) => {
  const active = document.activeElement;
  const editing = active && active.matches && active.matches(editableSelector);
  const suppressUntil = Number(sessionStorage.getItem(storageKeys.suppressUntil) || "0");
  return isFormDirty(state) || editing || Date.now() < suppressUntil;
};

export const markSubmittedConfigAnchor = (anchorId) => {
  sessionStorage.setItem(storageKeys.submittedConfigAnchor, anchorId || "");
};

export const markSubmittedActionAnchor = (anchorId) => {
  sessionStorage.setItem(storageKeys.submittedActionAnchor, anchorId || "");
};

export const getSubmittedConfigAnchor = () => (
  sessionStorage.getItem(storageKeys.submittedConfigAnchor) || ""
);

export const getSubmittedActionAnchor = () => (
  sessionStorage.getItem(storageKeys.submittedActionAnchor) || ""
);

export const clearSubmittedConfigAnchor = () => {
  sessionStorage.removeItem(storageKeys.submittedConfigAnchor);
};

export const clearSubmittedActionAnchor = () => {
  sessionStorage.removeItem(storageKeys.submittedActionAnchor);
};
