"""Google identity-token verification for login."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass


class GoogleAuthError(RuntimeError):
    """Raised when a Google credential cannot be verified."""


@dataclass(frozen=True)
class GoogleIdentity:
    """Verified identity claims from Google."""

    google_sub: str
    email: str
    display_name: str
    avatar_url: str


def verify_google_id_token(credential: str) -> GoogleIdentity:
    """Verify a Google ID token and return normalized identity claims.

    Production verification uses Google's tokeninfo endpoint. Tests and local
    demos can provide a JSON credential when AUTH_ALLOW_DEV_GOOGLE_TOKEN=true.
    """
    if os.getenv("AUTH_ALLOW_DEV_GOOGLE_TOKEN", "").lower() == "true":
        try:
            payload = json.loads(credential)
        except json.JSONDecodeError:
            payload = {}
        if payload.get("sub") and payload.get("email"):
            return _claims_to_identity(payload)

    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise GoogleAuthError("GOOGLE_CLIENT_ID is not configured")

    query = urllib.parse.urlencode({"id_token": credential})
    url = f"https://oauth2.googleapis.com/tokeninfo?{query}"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise GoogleAuthError("Unable to verify Google credential") from exc

    if payload.get("aud") != client_id:
        raise GoogleAuthError("Google credential audience does not match GOOGLE_CLIENT_ID")
    if payload.get("email_verified") not in ("true", True):
        raise GoogleAuthError("Google email is not verified")
    return _claims_to_identity(payload)


def _claims_to_identity(payload: dict) -> GoogleIdentity:
    if not payload.get("sub") or not payload.get("email"):
        raise GoogleAuthError("Google credential is missing required identity claims")
    return GoogleIdentity(
        google_sub=str(payload["sub"]),
        email=str(payload["email"]),
        display_name=str(payload.get("name") or payload.get("email") or ""),
        avatar_url=str(payload.get("picture") or ""),
    )
