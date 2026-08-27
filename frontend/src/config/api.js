export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const apiUrl = (path = '') => {
  if (!path) return API_BASE;
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
};

export const staticUrl = (path) => {
  if (!path) return path;
  return path.startsWith('/static/') ? apiUrl(path) : path;
};
