import { csrfHeaders } from "/static/dashboard/csrf.js";
import { renderSidebarStatus } from "/static/dashboard/sidebar_status.js";
import { bindDialogDismiss, openDialog } from "/static/dashboard/utils.js";

const pageSize = 50;
const emptyHitRecordsHtml = `
  <div class="preview-empty">
    <p>尚無命中紀錄</p>
    <span>符合關鍵字的貼文或留言會保存於此。</span>
  </div>
`;

const hitRecordText = (value) => (
  value === null || value === undefined || value === "" ? "未記錄" : String(value)
);

const appendHitRecordField = (container, labelText, value) => {
  const item = document.createElement("div");
  item.className = "hit-record-field field-grid-cell";
  const label = document.createElement("dt");
  label.textContent = `${labelText}：`;
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
    const empty = document.createElement("div");
    empty.className = "preview-empty";
    const title = document.createElement("p");
    title.textContent = "尚無命中紀錄";
    const description = document.createElement("span");
    description.textContent = "符合關鍵字的內容會出現在這裡，且可從原文連結回到 Facebook。";
    empty.appendChild(title);
    empty.appendChild(description);
    list.appendChild(empty);
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
    fields.className = "hit-record-fields field-grid field-grid--stacked";
    appendHitRecordField(fields, "類型", item.item_type);
    appendHitRecordField(fields, "作者", item.author_name);
    appendHitRecordField(fields, "關鍵字", item.matched_keyword);
    appendHitRecordField(fields, "記錄時間", item.recorded_at || item.notified_at);
    const contentBlock = document.createElement("div");
    contentBlock.className = "hit-record-content";
    const contentLabel = document.createElement("span");
    contentLabel.textContent = "內容：";
    const content = document.createElement("p");
    appendTextSegments(content, item.content, item.content_segments);
    contentBlock.appendChild(contentLabel);
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
        showToast(error.message || "命中紀錄載入失敗", "error");
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
      const confirmed = window.confirm(
        "確定要清空此 target 的所有命中紀錄嗎？\n此操作不會刪除 target，也不會清除最近掃描結果或設定。",
      );
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
          hitPanel.innerHTML = emptyHitRecordsHtml;
        }
        showToast("命中紀錄已清空", "success");
      } catch (error) {
        showToast(error.message || "清空紀錄失敗", "error");
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
        showToast(error.message || "命中紀錄載入失敗", "error");
      });
    });
  });
};
