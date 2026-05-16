import {
  getSidebarLinks,
  getTargetCards,
} from "/static/dashboard/sidebar_dom.js";

let activeSidebarAnchorId = "";
let sidebarJumpLock = null;
let sidebarIntersectionObserver = null;

const sidebarModuleVersion = (() => {
  try {
    return new URL(import.meta.url).searchParams.get("v") || "unversioned";
  } catch (error) {
    return "unknown";
  }
})();

const isScrollable = (element) => {
  if (!element || element === document || element === window) return false;
  const style = window.getComputedStyle(element);
  return (
    /(auto|scroll|overlay)/.test(style.overflowY)
    && element.scrollHeight > element.clientHeight
  );
};

const getScrollableAncestors = (node) => {
  const ancestors = [];
  let current = node?.parentElement;
  while (current && current !== document.body) {
    if (isScrollable(current)) {
      ancestors.push(current);
    }
    current = current.parentElement;
  }
  return ancestors;
};

const getScrollEventTargets = () => {
  const targets = new Set([
    window,
    document,
    document.scrollingElement,
    document.documentElement,
    document.body,
  ]);
  const targetList = document.querySelector(".target-list");
  [targetList, ...getTargetCards()].filter(Boolean).forEach((node) => {
    if (isScrollable(node)) {
      targets.add(node);
    }
    getScrollableAncestors(node).forEach((ancestor) => targets.add(ancestor));
  });
  return Array.from(targets).filter(Boolean);
};

const getTargetDistanceFromViewportCenter = (target) => {
  const rect = target.getBoundingClientRect();
  const focusY = window.innerHeight / 2;
  return Math.abs((rect.top + rect.height / 2) - focusY);
};

const findTargetCardNearViewportCenter = () => {
  const cards = getTargetCards();
  if (!cards.length) return null;

  const focusY = window.innerHeight / 2;
  let bestCard = null;
  let bestScore = Number.POSITIVE_INFINITY;

  cards.forEach((card) => {
    const rect = card.getBoundingClientRect();
    if (rect.height <= 0 || rect.bottom < 0 || rect.top > window.innerHeight) {
      return;
    }

    const containsFocus = rect.top <= focusY && rect.bottom >= focusY;
    const centerY = rect.top + rect.height / 2;
    const distance = Math.abs(centerY - focusY);
    const score = containsFocus ? distance / 100000 : distance;
    if (score < bestScore) {
      bestScore = score;
      bestCard = card;
    }
  });

  return bestCard;
};

export const activateSidebarTarget = (anchorId, { reveal = false } = {}) => {
  const alreadyActive = activeSidebarAnchorId === anchorId;
  activeSidebarAnchorId = anchorId;
  let activeLink = null;
  getSidebarLinks().forEach((link) => {
    const active = link.dataset.sidebarTarget === anchorId;
    link.classList.toggle("active", active);
    if (active) {
      link.setAttribute("aria-current", "true");
      activeLink = link;
    } else {
      link.removeAttribute("aria-current");
    }
  });

  const sidebar = activeLink?.closest(".target-sidebar");
  const sidebarIsFixed = sidebar && window.getComputedStyle(sidebar).position === "fixed";
  if (reveal && activeLink && sidebarIsFixed && !alreadyActive) {
    activeLink.scrollIntoView({ block: "nearest" });
  }
};

const setupSidebarScrollSync = () => {
  if (!getSidebarLinks().length) return;

  let frameId = 0;
  const syncActiveTarget = () => {
    frameId = 0;
    if (sidebarJumpLock) {
      activateSidebarTarget(sidebarJumpLock.anchorId);
      return;
    }
    const activeCard = findTargetCardNearViewportCenter();
    if (activeCard?.id) {
      activateSidebarTarget(activeCard.id, { reveal: true });
    }
  };

  const scheduleSync = () => {
    if (frameId) return;
    frameId = window.requestAnimationFrame(syncActiveTarget);
  };

  getScrollEventTargets().forEach((target) => {
    target.addEventListener("scroll", scheduleSync, { passive: true });
  });
  window.addEventListener("wheel", scheduleSync, { passive: true });
  window.addEventListener("touchmove", scheduleSync, { passive: true });
  window.addEventListener("keyup", scheduleSync);
  window.addEventListener("resize", scheduleSync);
  if ("IntersectionObserver" in window) {
    if (sidebarIntersectionObserver) {
      sidebarIntersectionObserver.disconnect();
    }
    sidebarIntersectionObserver = new IntersectionObserver(scheduleSync, {
      root: null,
      threshold: [0, 0.2, 0.5, 0.8, 1],
    });
    getTargetCards().forEach((card) => sidebarIntersectionObserver.observe(card));
  }
  window.__facebookMonitorDebug = window.__facebookMonitorDebug || {};
  window.__facebookMonitorDebug.sidebarSync = {
    forceSync: scheduleSync,
    getActiveAnchor: () => activeSidebarAnchorId,
    getCards: getTargetCards,
    getModuleVersion: () => sidebarModuleVersion,
    getScrollEventTargets,
  };
  scheduleSync();
};

const settleSidebarJumpLock = () => {
  if (!sidebarJumpLock) return;

  const target = document.getElementById(sidebarJumpLock.anchorId);
  const elapsedMs = window.performance.now() - sidebarJumpLock.startedAt;
  if (!target || getTargetDistanceFromViewportCenter(target) <= 36 || elapsedMs >= 1400) {
    sidebarJumpLock = null;
    const activeCard = findTargetCardNearViewportCenter();
    if (activeCard?.id) {
      activateSidebarTarget(activeCard.id, { reveal: true });
    }
    return;
  }

  window.requestAnimationFrame(settleSidebarJumpLock);
};

const lockSidebarDuringJump = (anchorId) => {
  sidebarJumpLock = {
    anchorId,
    startedAt: window.performance.now(),
  };
  window.requestAnimationFrame(settleSidebarJumpLock);
};

export const setupSidebar = () => {
  getSidebarLinks().forEach((link) => {
    link.addEventListener("click", (event) => {
      const anchorId = link.dataset.sidebarTarget || "";
      const target = document.getElementById(anchorId);
      if (!target) return;
      event.preventDefault();
      activateSidebarTarget(anchorId);
      lockSidebarDuringJump(anchorId);
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("jump-highlight");
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}`,
      );
      window.setTimeout(() => {
        if (document.body.contains(target)) {
          target.classList.remove("jump-highlight");
        }
      }, 1200);
    });
  });

  if (window.location.hash) {
    activateSidebarTarget(window.location.hash.slice(1));
  } else {
    const firstSidebarTarget = getSidebarLinks()[0];
    if (firstSidebarTarget) {
      activateSidebarTarget(firstSidebarTarget.dataset.sidebarTarget || "");
    }
  }

  setupSidebarScrollSync();
};
