const appendEmptyHint = (container, emptyText) => {
  const hint = document.createElement("span");
  if (emptyText === "尚無掃描紀錄") {
    hint.textContent = "啟動 target 後，最近掃描到的內容會顯示在這裡。";
  } else if (emptyText === "尚無命中紀錄") {
    hint.textContent = "符合關鍵字的貼文或留言會保存於此。";
  }
  if (hint.textContent) {
    container.appendChild(hint);
  }
};

export const renderPreviewRows = (rows, emptyText) => {
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "preview-empty";
    const message = document.createElement("p");
    message.textContent = emptyText;
    empty.appendChild(message);
    appendEmptyHint(empty, emptyText);
    return empty;
  }

  const list = document.createElement("ol");
  list.className = "preview-list";
  rows.forEach((row) => {
    list.appendChild(renderPreviewRow(row));
  });
  return list;
};

const renderPreviewRow = (row) => {
  const item = document.createElement("li");
  item.className = "preview-row";

  const main = document.createElement("div");
  main.className = "preview-row-main";
  item.appendChild(main);

  const copy = document.createElement("div");
  copy.className = "preview-row-copy";
  main.appendChild(copy);

  const header = document.createElement("div");
  header.className = "preview-row-header";
  copy.appendChild(header);

  const author = document.createElement("span");
  author.className = "preview-author";
  author.textContent = row.author_name || "(unknown)";
  header.appendChild(author);

  const badge = document.createElement("span");
  badge.className = `preview-badge ${row.badge_kind || ""}`.trim();
  badge.textContent = row.badge_text || "";
  header.appendChild(badge);

  if (row.content_preview) {
    const content = document.createElement("p");
    content.textContent = row.content_preview;
    copy.appendChild(content);
  }

  const linkWrapper = document.createElement("div");
  linkWrapper.className = "preview-row-link";
  main.appendChild(linkWrapper);

  if (row.permalink) {
    const link = document.createElement("a");
    link.href = row.permalink;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = row.link_label || "開啟連結";
    linkWrapper.appendChild(link);
  } else {
    const missing = document.createElement("span");
    missing.className = "missing-link";
    missing.textContent = "未取得連結";
    linkWrapper.appendChild(missing);
  }

  return item;
};
