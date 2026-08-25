export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '');

export const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || '';

/**
 * Local-only sign-in bypass. When enabled the app boots straight into the
 * workspace with a synthetic user instead of the sign-in landing page.
 *
 * Requests still go out unauthenticated, so the backend must also be running
 * with SCOPE_REQUIRE_AUTH unset/false — it then serves anonymous runs. Never
 * enable this in a deployed environment.
 */
export const DEV_AUTH_BYPASS =
  import.meta.env.DEV && import.meta.env.VITE_DEV_AUTH_BYPASS === 'true';

export const DEV_BYPASS_USER = {
  id: '',
  email: 'local@dev',
  displayName: 'Local Dev',
  avatarUrl: '',
};
