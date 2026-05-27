import { OnboardingAnswers, OnboardingProfile } from '../types/api';
import { authHeaders } from './authApi';
import { API_BASE } from './config';

export const fetchOnboardingProfile = async (token: string): Promise<OnboardingProfile | null> => {
  const resp = await fetch(`${API_BASE}/api/v1/onboarding/profile`, {
    credentials: 'include',
    headers: authHeaders(token),
  });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`Failed to load onboarding profile (${resp.status})`);
  return resp.json();
};

export const saveOnboardingProfile = async (
  token: string,
  answers: OnboardingAnswers,
): Promise<OnboardingProfile> => {
  const resp = await fetch(`${API_BASE}/api/v1/onboarding/profile`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(token),
    },
    body: JSON.stringify({ answers }),
  });
  if (!resp.ok) throw new Error(`Failed to save onboarding profile (${resp.status})`);
  return resp.json();
};
