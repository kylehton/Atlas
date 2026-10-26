import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class CredentialDecryptionError(RuntimeError):
    """Hide whether encrypted credentials were corrupt or used with the wrong key."""


class OAuthStateValidationError(PermissionError):
    """Reject malformed, expired, or incorrectly signed OAuth state."""


@dataclass(frozen=True, slots=True)
class VerifiedOAuthState:
    request_id: UUID
    user_id: UUID


class _OAuthStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    user_id: UUID
    nonce: str = Field(min_length=16, max_length=128)
    expires_at: int = Field(gt=0)


class CredentialCipher:
    """Encrypt provider refresh tokens before they enter the database."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, credential: str) -> bytes:
        return self._fernet.encrypt(credential.encode("utf-8"))

    def decrypt(self, encrypted_credential: bytes) -> str:
        try:
            return self._fernet.decrypt(encrypted_credential).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError):
            raise CredentialDecryptionError from None


class OAuthStateSigner:
    """Sign compact OAuth state while the database enforces one-time use."""

    def __init__(self, credential_key: str) -> None:
        raw_key = base64.urlsafe_b64decode(credential_key.encode("ascii"))
        self._signing_key = hmac.digest(raw_key, b"atlas-google-oauth-state-v1", "sha256")

    def sign(self, *, request_id: UUID, user_id: UUID, expires_at: datetime) -> str:
        payload = _OAuthStatePayload(
            request_id=request_id,
            user_id=user_id,
            nonce=secrets.token_urlsafe(24),
            expires_at=int(expires_at.timestamp()),
        )
        encoded_payload = _base64url(payload.model_dump_json().encode("utf-8"))
        signature = _base64url(hmac.digest(self._signing_key, encoded_payload, "sha256"))
        return f"{encoded_payload.decode('ascii')}.{signature.decode('ascii')}"

    def verify(self, state: str, *, now: datetime) -> VerifiedOAuthState:
        try:
            encoded_payload_text, encoded_signature_text = state.split(".", maxsplit=1)
            encoded_payload = encoded_payload_text.encode("ascii")
            supplied_signature = _unbase64url(encoded_signature_text)
            expected_signature = hmac.digest(self._signing_key, encoded_payload, "sha256")
            if not secrets.compare_digest(supplied_signature, expected_signature):
                raise OAuthStateValidationError
            payload = _OAuthStatePayload.model_validate_json(_unbase64url(encoded_payload_text))
        except (
            UnicodeEncodeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
            OAuthStateValidationError,
        ):
            raise OAuthStateValidationError from None

        if payload.expires_at <= int(now.timestamp()):
            raise OAuthStateValidationError
        return VerifiedOAuthState(request_id=payload.request_id, user_id=payload.user_id)


def _base64url(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _unbase64url(value: str) -> bytes:
    encoded = value.encode("ascii")
    return base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4))


def hash_oauth_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()
