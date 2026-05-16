const COUNTDOWN_SELECTOR = "[data-next-refresh]";
const SYNC_THRESHOLD_SECONDS = 1;
const SOON_LABEL = "下次刷新：即將刷新";

let countdownTimer = null;

const parseSeconds = (value) => {
  const seconds = Number.parseInt(String(value ?? "").trim(), 10);
  return Number.isFinite(seconds) ? seconds : null;
};

const formatCountdownSeconds = (seconds) => {
  const boundedSeconds = Math.max(Number.parseInt(seconds, 10) || 0, 0);
  if (boundedSeconds <= 0) {
    return "即將刷新";
  }
  if (boundedSeconds < 60) {
    return `${boundedSeconds}s`;
  }
  const minutes = Math.floor(boundedSeconds / 60);
  const remainder = boundedSeconds % 60;
  if (minutes < 60) {
    return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  const hourRemainder = minutes % 60;
  return hourRemainder ? `${hours}h ${hourRemainder}m` : `${hours}h`;
};

const currentRemainingSeconds = (node, now = Date.now()) => {
  const deadlineMs = Number.parseInt(node.dataset.nextRefreshDeadlineMs || "", 10);
  if (!Number.isFinite(deadlineMs)) {
    return null;
  }
  return Math.ceil((deadlineMs - now) / 1000);
};

const renderCountdown = (node, now = Date.now()) => {
  const remainingSeconds = currentRemainingSeconds(node, now);
  if (remainingSeconds === null) {
    return;
  }
  if (remainingSeconds <= 0) {
    delete node.dataset.nextRefreshDeadlineMs;
    delete node.dataset.nextRefreshSeconds;
    node.textContent = SOON_LABEL;
    stopTimerIfIdle();
    return;
  }
  node.textContent = `下次刷新：${formatCountdownSeconds(remainingSeconds)}`;
};

const activeCountdownNodes = () => (
  Array.from(document.querySelectorAll(`${COUNTDOWN_SELECTOR}[data-next-refresh-deadline-ms]`))
);

const tickCountdowns = () => {
  const now = Date.now();
  activeCountdownNodes().forEach((node) => {
    renderCountdown(node, now);
  });
};

const ensureTimer = () => {
  if (countdownTimer !== null) {
    return;
  }
  countdownTimer = window.setInterval(tickCountdowns, 1000);
};

const stopTimerIfIdle = () => {
  if (countdownTimer === null || activeCountdownNodes().length > 0) {
    return;
  }
  window.clearInterval(countdownTimer);
  countdownTimer = null;
};

const clearCountdown = (node, label) => {
  delete node.dataset.nextRefreshDeadlineMs;
  delete node.dataset.nextRefreshSeconds;
  if (label) {
    node.textContent = label;
  }
  stopTimerIfIdle();
};

export const syncNextRefreshCountdown = (node, seconds, label) => {
  if (!node) {
    return;
  }
  const incomingSeconds = parseSeconds(seconds);
  if (incomingSeconds === null) {
    clearCountdown(node, label || "");
    return;
  }
  if (incomingSeconds <= 0) {
    clearCountdown(node, SOON_LABEL);
    return;
  }

  const now = Date.now();
  const localSeconds = currentRemainingSeconds(node, now);
  if (
    localSeconds !== null
    && Math.abs(localSeconds - incomingSeconds) <= SYNC_THRESHOLD_SECONDS
  ) {
    node.dataset.nextRefreshSeconds = String(incomingSeconds);
    ensureTimer();
    return;
  }

  node.dataset.nextRefreshSeconds = String(incomingSeconds);
  node.dataset.nextRefreshDeadlineMs = String(now + incomingSeconds * 1000);
  renderCountdown(node, now);
  ensureTimer();
};

export const setupNextRefreshCountdowns = () => {
  document.querySelectorAll(COUNTDOWN_SELECTOR).forEach((node) => {
    syncNextRefreshCountdown(
      node,
      node.dataset.nextRefreshSeconds,
      node.textContent,
    );
  });
};
