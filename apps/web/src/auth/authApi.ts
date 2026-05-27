import { AuthResponse, AuthUser } from '../types/api';
import { API_BASE } from './config';

export const authHeaders = (token: string): HeadersInit => (token ? { Authorization: `Bearer ${token}` } : {});

export const loginWithGoogleCredential = async (credential: string): Promise<AuthResponse> => {
  const resp = await fetch(`${API_BASE}/api/v1/auth/google`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ credential }),
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '');
    throw new Error(`Google sign-in failed (${resp.status})${detail ? `: ${detail}` : ''}`);
  }
  return resp.json();
};

export const fetchCurrentUser = async (token: string): Promise<AuthUser> => {
  const resp = await fetch(`${API_BASE}/api/v1/auth/me`, {
    credentials: 'include',
    headers: authHeaders(token),
  });
  if (!resp.ok) {
    throw new Error(`Session lookup failed (${resp.status})`);
  }
  return resp.json();
};

export const logoutSession = async (token: string) => {
  await fetch(`${API_BASE}/api/v1/auth/logout`, {
    method: 'POST',
    credentials: 'include',
    headers: authHeaders(token),
  }).catch(() => undefined);
};
