const getSidebarLinks = () => Array.from(document.querySelectorAll("[data-sidebar-target]"));
let activeSidebarAnchorId = "";
let sidebarJumpLock = null;

const getTargetDistanceFromViewportCenter = (target) => {
  const rect = target.getBoundingClientRect();
  const focusY = window.innerHeight / 2;
  return Math.abs((rect.top + rect.height / 2) - focusY);
};

const findTargetCardNearViewportCenter = () => {
  const cards = Array.from(document.querySelectorAll("[data-target-card][id]"));
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

  window.addEventListener("scroll", scheduleSync, { passive: true });
  window.addEventListener("resize", scheduleSync);
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
