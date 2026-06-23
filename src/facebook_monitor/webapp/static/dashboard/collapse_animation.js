const fallbackCollapseAnimationMs = 240;
const collapseAnimationFallbackBufferMs = 80;

const parseCssDurationMs = (value) => {
  const normalized = value.trim().toLowerCase();
  const duration = Number.parseFloat(normalized);
  if (!Number.isFinite(duration)) return fallbackCollapseAnimationMs;
  if (normalized.endsWith("ms")) return duration;
  if (normalized.endsWith("s")) return duration * 1000;
  return duration;
};

const getCollapseAnimationDurationMs = (element) => {
  const styles = window.getComputedStyle?.(element);
  if (!styles) return fallbackCollapseAnimationMs;
  return parseCssDurationMs(styles.getPropertyValue("--collapse-panel-duration"));
};

const clearScheduledCollapseAnimation = (element) => {
  window.clearTimeout(Number(element.dataset.collapseAnimationTimer || "0"));
  if (typeof element.collapseTransitionEndHandler === "function") {
    element.removeEventListener("transitionend", element.collapseTransitionEndHandler);
    delete element.collapseTransitionEndHandler;
  }
  delete element.dataset.collapseAnimationTimer;
};

const finishCollapseAnimation = (element) => {
  element.removeAttribute("data-collapse-animating");
  element.style.height = "";
  element.style.opacity = "";
};

export const animateElementVisibility = (element, visible, { afterFinish = null } = {}) => {
  if (!element) return;
  clearScheduledCollapseAnimation(element);
  element.removeAttribute("data-collapse-animating");

  if (visible) {
    element.hidden = false;
    element.style.height = "0px";
    element.style.opacity = "0";
    element.setAttribute("data-collapse-animating", "true");
    element.getBoundingClientRect();
    element.style.height = `${element.scrollHeight}px`;
    element.style.opacity = "1";
  } else {
    element.style.height = `${element.scrollHeight}px`;
    element.style.opacity = "1";
    element.setAttribute("data-collapse-animating", "true");
    element.getBoundingClientRect();
    element.style.height = "0px";
    element.style.opacity = "0";
  }

  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    clearScheduledCollapseAnimation(element);
    if (!visible) {
      element.hidden = true;
    }
    finishCollapseAnimation(element);
    afterFinish?.();
  };
  const transitionEndHandler = (event) => {
    if (event.target === element && event.propertyName === "height") {
      finish();
    }
  };
  element.collapseTransitionEndHandler = transitionEndHandler;
  element.addEventListener("transitionend", transitionEndHandler);
  const timer = window.setTimeout(
    finish,
    getCollapseAnimationDurationMs(element) + collapseAnimationFallbackBufferMs,
  );
  element.dataset.collapseAnimationTimer = String(timer);
};
