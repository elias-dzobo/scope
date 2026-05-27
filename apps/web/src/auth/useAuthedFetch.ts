import { authHeaders } from './authApi';
import { useAuth } from './AuthProvider';

export const useAuthedFetch = () => {
  const { token } = useAuth();
  return (input: RequestInfo | URL, init: RequestInit = {}) => {
    const headers = {
      ...(init.headers ?? {}),
      ...authHeaders(token),
    };
    return fetch(input, { ...init, credentials: 'include', headers });
  };
};
