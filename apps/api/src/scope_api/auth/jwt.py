"""Small HS256 JWT helper for Scope API sessions.

The project avoids a new dependency for this first auth slice. The helper only
implements the JWT behavior we need: signed access tokens with issuer,
audience, subject, and expiry validation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


class JwtError(RuntimeError):
    """Raised when a Scope JWT cannot be trusted."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise JwtError("JWT_SECRET is not configured")
    return secret


def jwt_issuer() -> str:
    """Configured issuer claim."""
    return os.getenv("JWT_ISSUER", "scope")


def jwt_audience() -> str:
    """Configured audience claim."""
    return os.getenv("JWT_AUDIENCE", "scope-web")


def jwt_expiry_seconds() -> int:
    """Configured token lifetime."""
    minutes = os.getenv("JWT_EXPIRES_MINUTES", "1440")
    try:
        return max(1, int(minutes)) * 60
    except ValueError:
        return 1440 * 60


def create_access_token(*, user_id: str, email: str) -> str:
    """Create a signed access token for an authenticated user."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": jwt_issuer(),
        "aud": jwt_audience(),
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + jwt_expiry_seconds(),
    }
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(_jwt_secret().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Validate and decode a Scope access token."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
    except ValueError as exc:
        raise JwtError("Malformed token") from exc

    signing_input = f"{header_b64}.{payload_b64}"
    expected = hmac.new(_jwt_secret().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    actual = _b64url_decode(signature_b64)
    if not hmac.compare_digest(expected, actual):
        raise JwtError("Invalid token signature")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (json.JSONDecodeError, ValueError) as exc:
        raise JwtError("Invalid token payload") from exc

    if header.get("alg") != "HS256":
        raise JwtError("Unsupported token algorithm")
    now = int(time.time())
    if int(payload.get("exp", 0)) < now:
        raise JwtError("Token expired")
    if payload.get("iss") != jwt_issuer():
        raise JwtError("Invalid token issuer")
    if payload.get("aud") != jwt_audience():
        raise JwtError("Invalid token audience")
    if not payload.get("sub"):
        raise JwtError("Missing token subject")
    return payload
