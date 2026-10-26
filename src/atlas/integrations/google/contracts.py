from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

type GoogleCapabilityName = Literal["gmail", "calendar"]


class GoogleOAuthError(RuntimeError):
    """Represent a sanitized Google OAuth transport or response failure."""


class GoogleRefreshRejected(GoogleOAuthError):
    """Indicate that Google rejected a refresh token and reauthorization is required."""


@dataclass(frozen=True, slots=True)
class GoogleTokenGrant:
    access_token: str
    refresh_token: str | None
    scopes: frozenset[str] | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GoogleAccessGrant:
    access_token: str
    scopes: frozenset[str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str


class GoogleOAuth(Protocol):
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        """Build an incremental Google authorization URL with offline access."""

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> GoogleTokenGrant:
        """Exchange one authorization code without persisting its access token."""

    async def get_identity(self, access_token: str) -> GoogleIdentity:
        """Resolve the stable Google subject and verified account email."""

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenGrant:
        """Use a stored refresh token to obtain a memory-only access token."""

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        """Revoke a refresh token at Google."""
