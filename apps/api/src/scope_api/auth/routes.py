"""Auth routes for Google login and current-session lookup."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from scope_api import db
from scope_api.auth.dependencies import SESSION_COOKIE_NAME, require_current_user
from scope_api.auth.google import GoogleAuthError, verify_google_id_token
from scope_api.auth.jwt import JwtError, create_access_token
from scope_api.auth.models import AuthUser


class GoogleLoginRequest(BaseModel):
    """Credential posted by Google Identity Services on the frontend."""

    credential: str = Field(min_length=1)


class AuthResponse(BaseModel):
    """Successful auth response."""

    accessToken: str
    user: dict[str, str]


def _cookie_secure() -> bool:
    """Return whether auth cookies should require HTTPS."""
    return os.getenv("SCOPE_ENV", "local").strip().lower() == "production"


def _set_session_cookie(response: Response, token: str) -> None:
    """Set the revocable browser session cookie."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    """Clear the browser session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", httponly=True, secure=_cookie_secure(), samesite="lax")


def build_auth_router(prefix: str = "/api/v1") -> APIRouter:
    """Build auth routes under the configured API prefix."""
    router = APIRouter(prefix=f"{prefix}/auth", tags=["auth"])

    @router.post("/google", response_model=AuthResponse)
    async def login_with_google(payload: GoogleLoginRequest, request: Request, response: Response) -> AuthResponse:
        """Verify Google identity and issue a Scope JWT plus revocable session cookie."""
        try:
            identity = verify_google_id_token(payload.credential)
            user_row = db.upsert_user(
                email=identity.email,
                google_sub=identity.google_sub,
                display_name=identity.display_name,
                avatar_url=identity.avatar_url,
            )
            user = AuthUser.from_row(user_row)
            token = create_access_token(user_id=user.id, email=user.email)
            session_token, _session = db.create_user_session(
                user_id=user.id,
                user_agent=request.headers.get("user-agent", ""),
                ip_address=request.client.host if request.client else "",
            )
            _set_session_cookie(response, session_token)
        except (GoogleAuthError, JwtError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        return AuthResponse(accessToken=token, user=user.to_public_dict())

    @router.get("/me")
    async def get_me(user: AuthUser = Depends(require_current_user)) -> dict[str, str]:
        """Return the current authenticated user."""
        return user.to_public_dict()

    @router.post("/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        """Revoke the current browser session."""
        session_token = request.cookies.get(SESSION_COOKIE_NAME, "")
        if session_token:
            db.revoke_user_session(session_token)
        _clear_session_cookie(response)
        return {"ok": True}

    return router
