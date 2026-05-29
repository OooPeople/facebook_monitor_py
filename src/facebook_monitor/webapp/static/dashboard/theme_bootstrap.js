(() => {
  const allowedThemes = new Set(["light", "dark"]);
  const themeMeta = document.querySelector('meta[name="app-theme"]');
  let theme = themeMeta?.getAttribute("content") || "dark";
  if (!allowedThemes.has(theme)) {
    theme = "dark";
  }
  document.documentElement.dataset.theme = theme;
})();
