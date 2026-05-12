export const csrfToken = () => (
  document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || ""
);

export const csrfHeaders = (headers = {}) => ({
  ...headers,
  "X-CSRF-Token": csrfToken(),
});
