export const editableSelector = "input, textarea, select";

export const readJsonScript = (id, fallback) => {
  const node = document.getElementById(id);
  if (!node) return fallback;
  return JSON.parse(node.textContent || JSON.stringify(fallback));
};

export const showInlineStatus = (node, text, kind, timeoutMs = 0) => {
  if (!node) return;
  node.textContent = text;
  node.dataset.statusKind = kind;
  node.classList.add("is-visible");
  if (timeoutMs > 0) {
    window.setTimeout(() => {
      if (document.body.contains(node) && node.dataset.statusKind === kind) {
        node.classList.remove("is-visible");
      }
    }, timeoutMs);
  }
};

export const showToast = (text, kind = "success") => {
  if (!text) return;
  const stack = document.querySelector(".toast-stack");
  if (!stack) return;
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.textContent = text;
  stack.appendChild(toast);
  window.setTimeout(() => {
    if (document.body.contains(toast)) {
      toast.remove();
    }
  }, 3500);
};

export const closeDialog = (modal) => {
  if (!modal) return;
  if (typeof modal.close === "function") {
    modal.close();
  } else {
    modal.removeAttribute("open");
  }
};

export const openDialog = (modal) => {
  if (!modal) return;
  if (typeof modal.showModal === "function") {
    modal.showModal();
  } else {
    modal.setAttribute("open", "");
  }
};

export const clearFeedbackParams = (pageFeedback) => {
  if (!pageFeedback.message && !pageFeedback.error) return;
  const url = new URL(window.location.href);
  url.searchParams.delete("message");
  url.searchParams.delete("error");
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
};
