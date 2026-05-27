# OAuth Login Failure Runbook

1. Confirm `GOOGLE_CLIENT_ID` in `/etc/scope/scope.env`.
2. Confirm frontend build used `VITE_GOOGLE_CLIENT_ID`.
3. Confirm Google OAuth authorized origin includes frontend domain.
4. Confirm API CORS allows the frontend HTTPS origin.
5. Check browser devtools for blocked cookies or CORS errors.
6. Check API logs for `GoogleAuthError`.
