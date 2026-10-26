from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from atlas.integrations.google.contracts import (
    GoogleIdentity,
    GoogleOAuthError,
    GoogleRefreshRejected,
    GoogleTokenGrant,
)

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

type Clock = Callable[[], datetime]


class _GoogleTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1, max_length=16_384)
    refresh_token: str | None = Field(default=None, min_length=1, max_length=16_384)
    expires_in: int = Field(gt=0, le=86_400)
    scope: str | None = Field(default=None, max_length=16_384)


class _GoogleUserInfoResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: str = Field(min_length=1, max_length=256)
    email: str = Field(min_length=3, max_length=256)
    email_verified: bool


class GoogleOAuthClient:
    """Call Google's OAuth endpoints without logging or persisting bearer credentials."""

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret.get_secret_value()
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._clock = clock or (lambda: datetime.now(UTC))

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        parameters = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            }
        )
        return f"{GOOGLE_AUTHORIZE_URL}?{parameters}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> GoogleTokenGrant:
        return await self._request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def get_identity(self, access_token: str) -> GoogleIdentity:
        try:
            response = await self._client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            user_info = _GoogleUserInfoResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            raise GoogleOAuthError from None
        if not user_info.email_verified:
            raise GoogleOAuthError
        return GoogleIdentity(subject=user_info.sub, email=user_info.email)

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenGrant:
        return await self._request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            refresh=True,
        )

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        try:
            response = await self._client.post(
                GOOGLE_REVOKE_URL,
                data={"token": refresh_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise GoogleOAuthError from None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_token(
        self,
        form: dict[str, str],
        *,
        refresh: bool = False,
    ) -> GoogleTokenGrant:
        try:
            response = await self._client.post(GOOGLE_TOKEN_URL, data=form)
            if (
                refresh
                and response.status_code >= 400
                and _oauth_error(response) == "invalid_grant"
            ):
                raise GoogleRefreshRejected
            response.raise_for_status()
            token = _GoogleTokenResponse.model_validate(response.json())
        except GoogleRefreshRejected:
            raise
        except (httpx.HTTPError, ValueError, ValidationError):
            raise GoogleOAuthError from None
        return GoogleTokenGrant(
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            scopes=frozenset(token.scope.split()) if token.scope is not None else None,
            expires_at=self._clock() + timedelta(seconds=token.expires_in),
        )


def _oauth_error(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    return error if isinstance(error, str) else None
