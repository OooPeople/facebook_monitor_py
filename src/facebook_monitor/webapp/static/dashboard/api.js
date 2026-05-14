import { csrfHeaders } from "/static/dashboard/csrf.js";

export const jsonHeaders = () => csrfHeaders({ "Content-Type": "application/json" });

export const requestJson = async (url, { method = "POST", payload = {} } = {}) => {
  const response = await fetch(url, {
    method,
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (_error) {
      // keep status-only detail
    }
    throw new Error(detail);
  }
  return response.json();
};
