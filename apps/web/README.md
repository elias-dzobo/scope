# Scope Web

Vite frontend for Scope.

Local:

```bash
npm ci
npm run dev
```

Production build:

```bash
VITE_API_BASE_URL=https://api.scope.example.com \
VITE_GOOGLE_CLIENT_ID=<google-client-id> \
npm run build
```

The production build is served from `apps/web/dist` by Nginx. Browser auth uses
an HttpOnly session cookie; access tokens are not persisted in localStorage.
