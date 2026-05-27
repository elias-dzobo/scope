"""Auth-domain models shared by routes and dependencies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthUser:
    """Authenticated application user resolved from a Scope JWT."""

    id: str
    email: str
    display_name: str
    avatar_url: str

    @classmethod
    def from_row(cls, row: dict) -> "AuthUser":
        """Create an auth user from a database user row."""
        return cls(
            id=str(row["id"]),
            email=str(row["email"]),
            display_name=str(row.get("display_name") or ""),
            avatar_url=str(row.get("avatar_url") or ""),
        )

    def to_public_dict(self) -> dict[str, str]:
        """Return the frontend-safe user shape."""
        return {
            "id": self.id,
            "email": self.email,
            "displayName": self.display_name,
            "avatarUrl": self.avatar_url,
        }
