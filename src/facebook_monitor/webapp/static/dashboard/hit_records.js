import { csrfHeaders } from "/static/dashboard/csrf.js";
import { confirmDialog } from "/static/dashboard/dialogs.js";
import { renderSidebarStatus } from "/static/dashboard/sidebar_status.js";
import {
  bindDialogDismiss,
  formatClientErrorMessage,
  openDialog,
} from "/static/dashboard/utils.js";

const pageSize = 50;
const previewEmptyStates = {
  hitRecordsPreview: {
    title: "尚無命中紀錄",
    description: "符合關鍵字的貼文或留言會保存於此。",
  },
  hitRecordsModal: {
    title: "尚無命中紀錄",
    description: "符合關鍵字的內容會出現在這裡，且可從原文連結回到 Facebook。",
  },
};

const createPreviewEmpty = ({ title, description }) => {
  const empty = document.createElement("div");
  empty.className = "preview-empty";
  const titleNode = document.createElement("p");
  titleNode.textContent = title;
  const descriptionNode = document.createElement("span");
  descriptionNode.textContent = description;
  empty.appendChild(titleNode);
  empty.appendChild(descriptionNode);
  return empty;
};

const hitRecordText = (value) => (
  value === null || value === undefined || value === "" ? "未記錄" : String(value)
);

const appendHitRecordField = (container, labelText, value) => {
  const item = document.createElement("div");
  item.className = "hit-record-summary-item";
  const label = document.createElement("dt");
  label.textContent = labelText;
  const detail = document.createElement("dd");
  detail.textContent = hitRecordText(value);
  item.appendChild(label);
  item.appendChild(detail);
  container.appendChild(item);
};

const appendTextSegments = (container, fallbackText, segments) => {
  const segmentList = Array.isArray(segments) ? segments : [];
  if (segmentList.length === 0) {
    container.textContent = hitRecordText(fallbackText);
    return;
  }
  segmentList.forEach((segment) => {
    const text = hitRecordText(segment?.text);
    if (segment?.highlighted) {
      const mark = document.createElement("mark");
      mark.className = "keyword-highlight";
      mark.textContent = text;
      container.appendChild(mark);
      return;
    }
    container.appendChild(document.createTextNode(text));
  });
};

const updateHitCount = (targetId, totalCount) => {
  document.querySelectorAll(`[data-hit-count="${targetId}"]`).forEach((node) => {
    node.textContent = String(totalCount);
  });
  document.querySelectorAll(`[data-sidebar-status="${targetId}"]`).forEach((node) => {
    const baseStatus = node.dataset.sidebarBaseStatus || "";
    const detail = totalCount > 0 ? `命中 ${totalCount} 筆` : node.dataset.sidebarDefaultDetail || "";
    renderSidebarStatus(node, {
      baseStatus,
      statusDetail: detail,
    });
  });
  document
    .querySelectorAll(`[data-hit-records-modal="${targetId}"] [data-hit-records-total]`)
    .forEach((node) => {
      node.textContent = `命中紀錄 ${totalCount} 筆`;
    });
};

const updatePageStatus = (modal, payload, renderedCount) => {
  const status = modal.querySelector("[data-hit-records-page-status]");
  const loadMoreButton = modal.querySelector("[data-hit-records-load-more]");
  const totalCount = Number(payload.total_count || 0);
  if (status) {
    status.textContent = totalCount > 0
      ? `顯示最近 ${renderedCount} / ${totalCount} 筆`
      : "尚無命中紀錄";
  }
  if (loadMoreButton) {
    loadMoreButton.hidden = renderedCount >= totalCount;
    loadMoreButton.disabled = false;
  }
};

const renderHitRecords = (modal, payload, { append = false } = {}) => {
  const list = modal.querySelector("[data-hit-records-list]");
  if (!list) return;
  updateHitCount(String(payload.target_id || ""), Number(payload.total_count || 0));
  if (!append) {
    list.replaceChildren();
  }
  const items = Array.isArray(payload.items) ? payload.items : [];
  if (items.length === 0 && !append) {
    list.appendChild(createPreviewEmpty(previewEmptyStates.hitRecordsModal));
    updatePageStatus(modal, payload, 0);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("article");
    row.className = "hit-record-row";
    const sequence = document.createElement("div");
    sequence.className = "hit-record-sequence";
    sequence.textContent = `#${hitRecordText(item.sequence_number)}`;
    const fields = document.createElement("dl");
    fields.className = "hit-record-fields hit-record-summary-list";
    appendHitRecordField(fields, "類型", item.item_type);
    appendHitRecordField(fields, "作者", item.author_name);
    appendHitRecordField(fields, "關鍵字", item.matched_keyword);
    appendHitRecordField(fields, "記錄時間", item.recorded_at || item.notified_at);
    const contentBlock = document.createElement("div");
    contentBlock.className = "hit-record-content";
    const content = document.createElement("p");
    appendTextSegments(content, item.content, item.content_segments);
    contentBlock.appendChild(content);
    const actions = document.createElement("div");
    actions.className = "hit-record-actions";
    row.appendChild(sequence);
    row.appendChild(fields);
    row.appendChild(contentBlock);
    row.appendChild(actions);
    if (item.permalink) {
      const link = document.createElement("a");
      link.href = item.permalink;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "開啟連結";
      actions.appendChild(link);
    } else {
      const missing = document.createElement("span");
      missing.className = "missing-link";
      missing.textContent = "無連結";
      actions.appendChild(missing);
    }
    list.appendChild(row);
  });
  const renderedCount = list.querySelectorAll(".hit-record-row").length;
  modal.dataset.hitRecordsOffset = String(renderedCount);
  updatePageStatus(modal, payload, renderedCount);
};

const loadHitRecords = async (modal, targetId, { append = false } = {}) => {
  const list = modal.querySelector("[data-hit-records-list]");
  const loadMoreButton = modal.querySelector("[data-hit-records-load-more]");
  const offset = append ? Number(modal.dataset.hitRecordsOffset || "0") : 0;
  if (list && !append) {
    list.textContent = "載入中";
  }
  if (loadMoreButton) {
    loadMoreButton.disabled = true;
  }
  const response = await fetch(`/api/targets/${targetId}/hit-records?limit=${pageSize}&offset=${offset}`, {
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error("命中紀錄載入失敗");
  }
  renderHitRecords(modal, await response.json(), { append });
};

export const setupHitRecords = ({ showToast }) => {
  document.querySelectorAll("[data-view-records-button]").forEach((button) => {
    button.addEventListener("click", () => {
      const targetId = button.dataset.targetId || "";
      if (!targetId) return;
      const modal = document.querySelector(`[data-hit-records-modal="${targetId}"]`);
      if (!modal) return;
      openDialog(modal);
      loadHitRecords(modal, targetId).catch((error) => {
        showToast(formatClientErrorMessage(error, "命中紀錄載入失敗"), "error");
      });
    });
  });

  bindDialogDismiss({
    modalSelector: "[data-hit-records-modal]",
    closeSelector: "[data-close-hit-records]",
  });

  document.querySelectorAll("[data-clear-hit-records]").forEach((button) => {
    button.addEventListener("click", async () => {
      const targetId = button.dataset.targetId || "";
      const modal = button.closest("[data-hit-records-modal]");
      if (!targetId || !modal) return;
      const confirmed = await confirmDialog({
        title: "清空命中紀錄",
        message: "此操作不會刪除 target，也不會清除最近掃描結果或設定。",
        confirmLabel: "清空",
        danger: true,
      });
      if (!confirmed) return;
      try {
        const response = await fetch(`/api/targets/${targetId}/hit-records`, {
          method: "DELETE",
          headers: csrfHeaders(),
        });
        if (!response.ok) {
          throw new Error("清空紀錄失敗");
        }
        renderHitRecords(modal, await response.json());
        const target = document.getElementById(`target-${targetId}`);
        const hitPanel = target?.querySelector('[data-preview-panel="hits"]');
        if (hitPanel) {
          hitPanel.replaceChildren(createPreviewEmpty(previewEmptyStates.hitRecordsPreview));
        }
        showToast("命中紀錄已清空", "success");
      } catch (error) {
        showToast(formatClientErrorMessage(error, "清空紀錄失敗"), "error");
      }
    });
  });

  document.querySelectorAll("[data-hit-records-load-more]").forEach((button) => {
    button.addEventListener("click", () => {
      const targetId = button.dataset.targetId || "";
      const modal = button.closest("[data-hit-records-modal]");
      if (!targetId || !modal) return;
      loadHitRecords(modal, targetId, { append: true }).catch((error) => {
        button.disabled = false;
        showToast(formatClientErrorMessage(error, "命中紀錄載入失敗"), "error");
      });
    });
  });
};
