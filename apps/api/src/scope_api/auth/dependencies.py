"""FastAPI dependencies for optional and required user auth."""

from __future__ import annotations

from fastapi import Cookie, Header, HTTPException, status

from scope_api import db
from scope_api.auth.jwt import JwtError, decode_access_token
from scope_api.auth.models import AuthUser


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


SESSION_COOKIE_NAME = "scope_session"


def optional_current_user(
    authorization: str | None = Header(default=None),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> AuthUser | None:
    """Return the current user when a valid Bearer token or session cookie is supplied."""
    if session_token:
        row = db.get_user_by_session_token(session_token)
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
        return AuthUser.from_row(row)

    token = _bearer_token(authorization)
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except JwtError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    row = db.get_user(str(payload["sub"]))
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return AuthUser.from_row(row)


def require_current_user(
    authorization: str | None = Header(default=None),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> AuthUser:
    """Require a valid signed-in user."""
    user = optional_current_user(authorization, session_token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
