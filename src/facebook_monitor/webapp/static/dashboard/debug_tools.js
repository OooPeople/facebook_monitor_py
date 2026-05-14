export const setupDebugCopyButtons = () => {
  document.querySelectorAll(".debug-copy-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const defaultText = button.dataset.defaultText || button.textContent;
      const source = button.closest(".scan-debug-details, .debug-details")
        ?.querySelector(".debug-copy-source");
      const text = source ? source.value : "";
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "已複製";
      } catch (error) {
        if (source) {
          source.focus();
          source.select();
        }
        button.textContent = "請手動複製";
      }
      window.setTimeout(() => {
        if (document.body.contains(button)) {
          button.textContent = defaultText;
        }
      }, 1500);
    });
  });
};
