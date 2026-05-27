# Google OAuth Setup Guide

This guide explains how to configure real Google sign-in for Scope so we can test the platform as actual users.

Scope uses **Google Identity Services** in the frontend. The browser receives a Google ID token, posts it to the backend at `/api/v1/auth/google`, and the backend verifies the token audience against `GOOGLE_CLIENT_ID`. If valid, Scope creates or updates a local user row and returns a Scope JWT.

## 1. Create A Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project, for example `scope-dev`.
3. Open **APIs & Services → OAuth consent screen**.
4. Choose **External** unless this is only for a Google Workspace organization.
5. Fill in:
   - App name: `Scope`
   - User support email: your email
   - Developer contact email: your email
6. For local testing, keep the app in **Testing** mode.
7. Add yourself and other test accounts under **Test users**.

## 2. Create OAuth Client Credentials

1. Open **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. Application type: **Web application**.
4. Name: `Scope Web Local`.
5. Authorized JavaScript origins:

```text
http://localhost:3000
http://127.0.0.1:3000
```

6. For production, add the final frontend origin later:

```text
https://your-domain.com
https://www.your-domain.com
```

7. Click **Create**.
8. Copy the **Client ID**. Scope does not need the client secret for this browser-based ID-token flow.

## 3. Configure Scope Environment Variables

In the repo root `.env`:

```bash
GOOGLE_CLIENT_ID=your-google-oauth-client-id.apps.googleusercontent.com
VITE_GOOGLE_CLIENT_ID=your-google-oauth-client-id.apps.googleusercontent.com

JWT_SECRET=replace-with-a-long-random-secret
JWT_ISSUER=scope
JWT_AUDIENCE=scope-web
JWT_EXPIRES_MINUTES=1440
AUTH_ALLOW_DEV_GOOGLE_TOKEN=false
```

Important:

- `GOOGLE_CLIENT_ID` is used by the backend to verify token audience.
- `VITE_GOOGLE_CLIENT_ID` is used by the frontend to render the Google button.
- The two client IDs should match for normal browser login.
- `JWT_SECRET` should be at least 32 random bytes in production.

Generate a local secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 4. Run The App Locally

Backend:

```bash
uv run python api_main.py
```

Frontend:

```bash
cd apps/web
npm run dev
```

Open:

```text
http://localhost:3000
```

You should see the Google sign-in button in the top navigation or on profile/advisor screens.

## 5. Verify The Login Flow

After signing in:

1. Browser receives a Google credential from `https://accounts.google.com/gsi/client`.
2. Frontend calls:

```http
POST /api/v1/auth/google
```

3. Backend verifies:
   - token is valid
   - token `aud` equals `GOOGLE_CLIENT_ID`
   - Google email is verified
4. Backend upserts the user in `users`.
5. Backend returns:

```json
{
  "accessToken": "...scope-jwt...",
  "user": {
    "id": "...",
    "email": "...",
    "displayName": "...",
    "avatarUrl": "..."
  }
}
```

6. Frontend stores the Scope JWT and sends it as:

```http
Authorization: Bearer <accessToken>
```

## 6. Common Issues

### Google button does not show

Check:

- `VITE_GOOGLE_CLIENT_ID` is set before starting `npm run dev`.
- The frontend was restarted after editing `.env`.
- Browser console does not show blocked script errors for `accounts.google.com`.

### Backend returns `GOOGLE_CLIENT_ID is not configured`

Set `GOOGLE_CLIENT_ID` in the backend environment and restart `api_main.py`.

### Backend returns `Google credential audience does not match`

The frontend and backend are using different OAuth client IDs. Make sure:

```bash
GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID
```

for the same environment.

### Login works locally but not in production

Add the production frontend origin to the Google OAuth client:

```text
https://your-domain.com
https://www.your-domain.com
```

Google Identity Services validates the browser origin.

### Testing without Google

Only for local development, Scope supports JSON credentials when:

```bash
AUTH_ALLOW_DEV_GOOGLE_TOKEN=true
```

Do not enable this in production.

## 7. Production Security Checklist

- Use HTTPS only.
- Use a strong `JWT_SECRET`.
- Keep `AUTH_ALLOW_DEV_GOOGLE_TOKEN=false`.
- Restrict Google OAuth JavaScript origins to real frontend domains.
- Keep the OAuth consent screen test users limited until launch.
- Rotate `JWT_SECRET` if it leaks.
- Do not expose backend `.env` values to the frontend except `VITE_*` variables.
