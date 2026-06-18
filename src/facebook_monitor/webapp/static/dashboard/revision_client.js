import { applyDashboardPartialUpdate } from "/static/dashboard/partial_updates.js";
import { saveScrollPosition, shouldDelayRefresh } from "/static/dashboard/state.js";

const pollingIntervalMs = 3000;
const pendingRefreshCheckMs = 1000;
const safetyPollIntervalMs = 15000;
const sseFallbackDelayMs = 2500;

const transportStates = {
  sseConnecting: "sse_connecting",
  sseOpen: "sse_open",
  sseReconnecting: "sse_reconnecting",
  fallbackPolling: "fallback_polling",
  closed: "closed",
};

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

const createRevisionRuntime = () => ({
  source: null,
  pollingIntervalId: 0,
  fallbackTimerId: 0,
  pendingRefreshIntervalId: 0,
  safetyPollIntervalId: 0,
  closed: false,
});

const setSseState = (state, revisionTransportState) => {
  state.revisionTransport = "sse";
  state.revisionTransportState = revisionTransportState;
};

const setPollingState = (state) => {
  state.revisionTransport = "polling";
  state.revisionTransportState = transportStates.fallbackPolling;
};

const clearFallbackTimer = (runtime) => {
  if (!runtime.fallbackTimerId) return;
  window.clearTimeout(runtime.fallbackTimerId);
  runtime.fallbackTimerId = 0;
};

const startPollingFallback = (state, runtime) => {
  if (runtime.closed) return;
  setPollingState(state);
  if (runtime.pollingIntervalId) return;
  runtime.pollingIntervalId = window.setInterval(
    () => pollRevision(state),
    pollingIntervalMs,
  );
  void pollRevision(state);
};

const stopPollingFallback = (runtime) => {
  if (!runtime.pollingIntervalId) return;
  window.clearInterval(runtime.pollingIntervalId);
  runtime.pollingIntervalId = 0;
};

const schedulePollingFallback = (state, runtime) => {
  if (runtime.closed || runtime.fallbackTimerId || runtime.pollingIntervalId) return;
  runtime.fallbackTimerId = window.setTimeout(() => {
    runtime.fallbackTimerId = 0;
    if (!state.sseConnected && !runtime.closed) {
      startPollingFallback(state, runtime);
    }
  }, sseFallbackDelayMs);
};

const selectedRevisionTransport = () => {
  const configured = String(window.__DASHBOARD_REVISION_TRANSPORT__ || "sse").toLowerCase();
  return configured === "polling" ? "polling" : "sse";
};

const setupSseRevisionEvents = (state, runtime) => {
  if (selectedRevisionTransport() === "polling") {
    startPollingFallback(state, runtime);
    return;
  }
  if (!("EventSource" in window)) {
    startPollingFallback(state, runtime);
    return;
  }
  if (runtime.source) return;

  setSseState(state, transportStates.sseConnecting);
  const source = new EventSource("/api/dashboard-events");
  runtime.source = source;
  schedulePollingFallback(state, runtime);

  source.addEventListener("open", () => {
    if (runtime.closed) return;
    state.sseConnected = true;
    setSseState(state, transportStates.sseOpen);
    clearFallbackTimer(runtime);
    stopPollingFallback(runtime);
  });

  source.addEventListener("dashboard_revision", (event) => {
    if (runtime.closed) return;
    state.lastSseEventAt = Date.now();
    try {
      void handleRevisionPayload(state, JSON.parse(event.data || "{}"));
    } catch (error) {
      return;
    }
  });

  source.addEventListener("error", () => {
    if (runtime.closed) return;
    state.sseConnected = false;
    if (runtime.pollingIntervalId) {
      setPollingState(state);
      clearFallbackTimer(runtime);
      return;
    }
    setSseState(state, transportStates.sseReconnecting);
    schedulePollingFallback(state, runtime);
  });
};

const teardownRevisionClient = (state, runtime) => {
  if (runtime.closed) return;
  runtime.closed = true;
  clearFallbackTimer(runtime);
  stopPollingFallback(runtime);
  if (runtime.pendingRefreshIntervalId) {
    window.clearInterval(runtime.pendingRefreshIntervalId);
    runtime.pendingRefreshIntervalId = 0;
  }
  if (runtime.safetyPollIntervalId) {
    window.clearInterval(runtime.safetyPollIntervalId);
    runtime.safetyPollIntervalId = 0;
  }
  if (runtime.source) {
    runtime.source.close();
    runtime.source = null;
  }
  state.sseConnected = false;
  state.revisionTransportState = transportStates.closed;
};

export const setupRevisionClient = (state) => {
  const runtime = createRevisionRuntime();

  setupSseRevisionEvents(state, runtime);

  runtime.pendingRefreshIntervalId = window.setInterval(() => {
    if (state.pendingRefresh && !shouldDelayRefresh(state)) {
      state.pendingRefresh = false;
      void updateWhenSafe(state);
    }
  }, pendingRefreshCheckMs);
  runtime.safetyPollIntervalId = window.setInterval(() => {
    if (
      state.revisionTransport === "polling"
      && !runtime.pollingIntervalId
      && !shouldDelayRefresh(state)
      && !state.pendingRefresh
    ) {
      pollRevision(state);
    }
  }, safetyPollIntervalMs);

  const teardown = () => teardownRevisionClient(state, runtime);
  window.addEventListener("pagehide", teardown, { once: true });
  window.addEventListener("beforeunload", teardown, { once: true });
  return teardown;
};
