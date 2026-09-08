import hashlib
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from atlas.shared.field_types import ExternalId

TELEGRAM_OIDC_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OIDC_AUTHORIZE_URL = f"{TELEGRAM_OIDC_ISSUER}/auth"
TELEGRAM_OIDC_TOKEN_URL = f"{TELEGRAM_OIDC_ISSUER}/token"
TELEGRAM_OIDC_JWKS_URL = f"{TELEGRAM_OIDC_ISSUER}/.well-known/jwks.json"


class TelegramAuthenticationError(PermissionError):
    """Represent a sanitized Telegram OIDC transport or validation failure."""


@dataclass(frozen=True, slots=True)
class VerifiedTelegramIdentity:
    provider_user_id: ExternalId


class TelegramBrowserAuth(Protocol):
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Build a Telegram authorization URL for one browser login attempt."""

    async def authenticate(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce_hash: str,
    ) -> VerifiedTelegramIdentity:
        """Exchange and verify Telegram's callback, returning its stable user identity."""


class _TelegramTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id_token: str = Field(min_length=1, max_length=16_384)


class _TelegramIdClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: str = Field(min_length=1, max_length=256)
    id: int = Field(gt=0)
    nonce: str = Field(min_length=1, max_length=256)


class TelegramOidcClient:
    """Authenticate browser users using Telegram's OIDC code flow with PKCE."""

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret.get_secret_value()
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        parameters = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{TELEGRAM_OIDC_AUTHORIZE_URL}?{parameters}"

    async def authenticate(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce_hash: str,
    ) -> VerifiedTelegramIdentity:
        """Exchange the code and verify signature, issuer, audience, expiry, and nonce."""

        try:
            token_response = await self._client.post(
                TELEGRAM_OIDC_TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "code_verifier": code_verifier,
                },
            )
            token_response.raise_for_status()
            token = _TelegramTokenResponse.model_validate(token_response.json()).id_token
            jwks_response = await self._client.get(TELEGRAM_OIDC_JWKS_URL)
            jwks_response.raise_for_status()
            claims = self._verify_id_token(token, jwks_response.json())
        except (httpx.HTTPError, ValueError, ValidationError, jwt.PyJWTError):
            raise TelegramAuthenticationError from None

        nonce_hash = hashlib.sha256(claims.nonce.encode("utf-8")).hexdigest()
        if not secrets.compare_digest(nonce_hash, expected_nonce_hash):
            raise TelegramAuthenticationError
        # Telegram's profile `id` matches the Bot API sender ID used to bind this request.
        # The standard OIDC `sub` is also required above, but is a distinct subject claim.
        return VerifiedTelegramIdentity(provider_user_id=str(claims.id))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _verify_id_token(self, token: str, jwks_payload: object) -> _TelegramIdClaims:
        if not isinstance(jwks_payload, dict):
            raise TelegramAuthenticationError
        keys = jwks_payload.get("keys")
        if not isinstance(keys, list):
            raise TelegramAuthenticationError

        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(key_id, str):
            raise TelegramAuthenticationError
        key_payload = next(
            (key for key in keys if isinstance(key, dict) and key.get("kid") == key_id),
            None,
        )
        if key_payload is None:
            raise TelegramAuthenticationError
        signing_key = jwt.PyJWK.from_dict(key_payload).key
        decoded = jwt.decode(
            token,
            key=signing_key,
            algorithms=["RS256"],
            audience=self._client_id,
            issuer=TELEGRAM_OIDC_ISSUER,
        )
        return _TelegramIdClaims.model_validate(decoded)
