import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import {
  fetchCurrentUser,
  loginWithGoogleCredential,
  logoutSession,
} from './authApi';
import { AuthUser } from '../types/api';
import { GOOGLE_CLIENT_ID } from './config';

declare global {
  interface Window {
    google?: {
      accounts?: {
        id?: {
          initialize: (options: {
            client_id: string;
            callback: (response: { credential?: string }) => void;
            ux_mode?: 'popup' | 'redirect';
          }) => void;
          renderButton: (element: HTMLElement, options: Record<string, unknown>) => void;
        };
      };
    };
  }
}

interface AuthContextValue {
  token: string;
  user: AuthUser | null;
  loading: boolean;
  googleReady: boolean;
  authError: string;
  signOut: () => void;
  clearAuthError: () => void;
  renderGoogleButton: (element: HTMLElement) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [token, setToken] = useState('');
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [googleReady, setGoogleReady] = useState(false);
  const [authError, setAuthError] = useState('');

  useEffect(() => {
    setLoading(true);
    fetchCurrentUser(token)
      .then(setUser)
      .catch(() => {
        setToken('');
        setUser(null);
        if (token) {
          setAuthError('Your session expired. Please sign in again.');
        }
      })
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) {
      setAuthError('Google sign-in is not configured. Set VITE_GOOGLE_CLIENT_ID and restart the frontend.');
      setGoogleReady(false);
      return;
    }
    if (!GOOGLE_CLIENT_ID || window.google?.accounts?.id) {
      setGoogleReady(Boolean(window.google?.accounts?.id));
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.onload = () => setGoogleReady(Boolean(window.google?.accounts?.id));
    script.onerror = () => setAuthError('Google sign-in could not load. Check your network, browser blockers, or Google OAuth setup.');
    document.head.appendChild(script);
  }, []);

  const signOut = async () => {
    await logoutSession(token);
    setToken('');
    setUser(null);
    setAuthError('');
  };

  const clearAuthError = () => setAuthError('');

  const renderGoogleButton = (element: HTMLElement) => {
    if (!GOOGLE_CLIENT_ID || !window.google?.accounts?.id) return;
    setAuthError('');
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      ux_mode: 'popup',
      callback: async (response) => {
        try {
          if (!response.credential) {
            setAuthError('Google did not return a sign-in credential. Please try again.');
            return;
          }
          const auth = await loginWithGoogleCredential(response.credential);
          setToken(auth.accessToken);
          setUser(auth.user);
          setAuthError('');
        } catch (err) {
          setAuthError(err instanceof Error ? err.message : 'Google sign-in failed. Please try again.');
        }
      },
    });
    window.google.accounts.id.renderButton(element, {
      theme: 'outline',
      size: 'medium',
      text: 'signin_with',
      shape: 'rectangular',
    });
  };

  const value = useMemo<AuthContextValue>(
    () => ({ token, user, loading, googleReady, authError, signOut, clearAuthError, renderGoogleButton }),
    [token, user, loading, googleReady, authError],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return context;
};
