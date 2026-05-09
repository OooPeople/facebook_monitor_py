import { applyDashboardPartialUpdate } from "./partial_updates.js?v=ui-refactor-phase18-form-sidebar-status";
import { saveScrollPosition, shouldDelayRefresh } from "./state.js";

const pollingIntervalMs = 3000;
const pendingRefreshCheckMs = 1000;
const safetyPollIntervalMs = 15000;
const sseFallbackDelayMs = 2500;

const reloadWhenPartialUpdateFails = () => {
  saveScrollPosition();
  window.location.reload();
};

const updateWhenSafe = async (state) => {
  if (shouldDelayRefresh(state)) {
    state.pendingRefresh = true;
    return;
  }

  try {
    await applyDashboardPartialUpdate(state);
  } catch (error) {
    reloadWhenPartialUpdateFails();
  }
};

const handleRevisionPayload = async (state, payload) => {
  if (payload.revision && payload.revision !== state.currentRevision) {
    state.currentRevision = payload.revision;
    await updateWhenSafe(state);
  }
};

const pollRevision = async (state) => {
  try {
    const response = await fetch("/api/dashboard-revision", { cache: "no-store" });
    if (!response.ok) return;
    await handleRevisionPayload(state, await response.json());
  } catch (error) {
    return;
  }
};

const startPollingFallback = (state, pollingState) => {
  if (pollingState.intervalId) return;
  state.revisionTransport = "polling";
  pollingState.intervalId = window.setInterval(
    () => pollRevision(state),
    pollingIntervalMs,
  );
};

const stopPollingFallback = (pollingState) => {
  if (!pollingState.intervalId) return;
  window.clearInterval(pollingState.intervalId);
  pollingState.intervalId = 0;
};

const setupSseRevisionEvents = (state, pollingState) => {
  if (!("EventSource" in window)) {
    startPollingFallback(state, pollingState);
    return;
  }

  const source = new EventSource("/api/dashboard-events");
  state.revisionTransport = "sse";

  const fallbackTimer = window.setTimeout(() => {
    if (!state.sseConnected) {
      startPollingFallback(state, pollingState);
    }
  }, sseFallbackDelayMs);

  source.addEventListener("open", () => {
    state.sseConnected = true;
    state.revisionTransport = "sse";
    stopPollingFallback(pollingState);
    window.clearTimeout(fallbackTimer);
  });

  source.addEventListener("dashboard_revision", (event) => {
    state.lastSseEventAt = Date.now();
    try {
      void handleRevisionPayload(state, JSON.parse(event.data || "{}"));
    } catch (error) {
      return;
    }
  });

  source.addEventListener("error", () => {
    state.sseConnected = false;
    startPollingFallback(state, pollingState);
  });

  const closeSource = () => {
    source.close();
    state.sseConnected = false;
  };
  window.addEventListener("pagehide", closeSource, { once: true });
  window.addEventListener("beforeunload", closeSource, { once: true });
};

export const setupRevisionClient = (state) => {
  const pollingState = { intervalId: 0 };

  setupSseRevisionEvents(state, pollingState);

  window.setInterval(() => {
    if (state.pendingRefresh && !shouldDelayRefresh(state)) {
      state.pendingRefresh = false;
      void updateWhenSafe(state);
    }
  }, pendingRefreshCheckMs);
  window.setInterval(() => {
    if (
      state.revisionTransport === "polling"
      && !pollingState.intervalId
      && !shouldDelayRefresh(state)
      && !state.pendingRefresh
    ) {
      pollRevision(state);
    }
  }, safetyPollIntervalMs);
};
