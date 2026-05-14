import { csrfHeaders } from "/static/dashboard/csrf.js";

const THEMES = new Set(["light", "dark"]);

const persistTheme = async (theme) => {
  const response = await fetch("/settings/theme", {
    method: "POST",
    headers: csrfHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({theme}),
  });
  if (!response.ok) {
    throw new Error("theme_save_failed");
  }
};

const currentPageTheme = () => {
  const pageTheme = document.documentElement.dataset.theme || "";
  if (THEMES.has(pageTheme)) return pageTheme;
  return "light";
};

const applyTheme = (theme) => {
  const normalizedTheme = THEMES.has(theme) ? theme : "light";
  document.documentElement.dataset.theme = normalizedTheme;
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    const isDark = normalizedTheme === "dark";
    const label = isDark ? "深色" : "淺色";
    button.setAttribute("aria-pressed", String(isDark));
    button.setAttribute("title", `目前：${label}`);
    const lightIcon = button.querySelector("[data-theme-icon-light]");
    const darkIcon = button.querySelector("[data-theme-icon-dark]");
    lightIcon?.toggleAttribute("hidden", isDark);
    darkIcon?.toggleAttribute("hidden", !isDark);
    const labelElement = button.querySelector("[data-theme-toggle-label]");
    if (labelElement) labelElement.textContent = label;
  });
};

export const setupThemeToggle = () => {
  applyTheme(currentPageTheme());
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const currentTheme = currentPageTheme();
      const nextTheme = currentTheme === "dark" ? "light" : "dark";
      applyTheme(nextTheme);
      persistTheme(nextTheme).catch(() => {
        // DB 儲存失敗時仍保留本頁立即切換；下次載入會回到 DB 內最後成功保存的值。
      });
    });
  });
};
