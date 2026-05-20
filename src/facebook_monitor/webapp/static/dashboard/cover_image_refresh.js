import { requestJson } from "/static/dashboard/api.js";

const reportedImageFailures = new Set();

const targetIdForImage = (image) => {
  const root = image.closest("[data-target-id]");
  return root?.dataset?.targetId || "";
};

const displayNameForImage = (image) => {
  const card = image.closest("[data-target-card]");
  const cardTitle = card?.querySelector("[data-target-title]")?.textContent || "";
  if (cardTitle.trim()) return cardTitle;
  const sidebarItem = image.closest("[data-sidebar-item]");
  return sidebarItem?.querySelector(".sidebar-name")?.textContent || "";
};

const fallbackAvatar = (image) => {
  const avatar = image.closest(".target-avatar, .sidebar-avatar");
  if (!avatar) return;
  const displayName = displayNameForImage(image).trim();
  avatar.classList.remove("has-image");
  avatar.textContent = (displayName || "?").slice(0, 1) || "?";
};

const reportLoadFailure = async ({ targetId, url, source }) => {
  await requestJson(
    `/api/targets/${encodeURIComponent(targetId)}/cover-image/load-failure`,
    {
      payload: { url, source },
    },
  );
};

const handleBrokenImage = (image) => {
  const targetId = targetIdForImage(image);
  const url = String(image.currentSrc || image.src || image.getAttribute("src") || "").trim();
  const source = image.closest("[data-sidebar-item]") ? "sidebar" : "card";
  fallbackAvatar(image);
  if (!targetId || !url) return;
  const reportKey = `${targetId}\n${url}`;
  if (reportedImageFailures.has(reportKey)) return;
  reportedImageFailures.add(reportKey);
  reportLoadFailure({ targetId, url, source }).catch(() => {
    // Broken thumbnails must not interrupt dashboard interactions.
  });
};

const handleImageError = (event) => {
  const image = event.target;
  if (!(image instanceof HTMLImageElement)) return;
  handleBrokenImage(image);
};

const scanAlreadyFailedImages = () => {
  document
    .querySelectorAll(".target-avatar img, .sidebar-avatar img")
    .forEach((image) => {
      if (!(image instanceof HTMLImageElement)) return;
      if (image.complete && image.naturalWidth === 0) {
        handleBrokenImage(image);
      }
    });
};

export const setupCoverImageRefresh = () => {
  document.addEventListener("error", handleImageError, true);
  scanAlreadyFailedImages();
  requestAnimationFrame(scanAlreadyFailedImages);
  window.setTimeout(scanAlreadyFailedImages, 500);
};
