// Shared client configuration.
// API base URL can be overridden at build time via VITE_API_BASE.
export const API_BASE: string =
  import.meta.env.VITE_API_BASE || 'http://localhost:3000/api';
