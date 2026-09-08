import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from atlas.telegram.browser_auth import (
    TELEGRAM_OIDC_ISSUER,
    TelegramAuthenticationError,
    TelegramOidcClient,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _base64url_integer(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.mark.anyio
@pytest.mark.parametrize("audience, succeeds", [("client-id", True), ("other-client", False)])
async def test_oidc_client_validates_telegram_identity(
    audience: str,
    succeeds: bool,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    public_numbers = private_key.public_key().public_numbers()
    nonce = "test-nonce"
    now = datetime.now(UTC)
    id_token = jwt.encode(
        {
            "iss": TELEGRAM_OIDC_ISSUER,
            "aud": audience,
            "sub": "oidc-subject-distinct-from-bot-id",
            "id": 123_456,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "nonce": nonce,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "use": "sig",
        "alg": "RS256",
        "n": _base64url_integer(public_numbers.n),
        "e": _base64url_integer(public_numbers.e),
    }

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"id_token": id_token})
        return httpx.Response(200, json={"keys": [jwk]})

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as http_client:
        telegram = TelegramOidcClient(
            "client-id",
            SecretStr("client-secret"),
            client=http_client,
        )
        authorization_url = telegram.authorization_url(
            redirect_uri="https://atlas.test/settings/auth/telegram/callback",
            state="test-state",
            nonce=nonce,
            code_challenge="test-challenge",
        )
        query = parse_qs(urlparse(authorization_url).query)
        assert query["state"] == ["test-state"]
        assert query["nonce"] == [nonce]
        assert query["code_challenge_method"] == ["S256"]

        authentication = telegram.authenticate(
            code="test-code",
            redirect_uri="https://atlas.test/settings/auth/telegram/callback",
            code_verifier="test-verifier",
            expected_nonce_hash=hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        )
        if succeeds:
            identity = await authentication
            assert identity.provider_user_id == "123456"
        else:
            with pytest.raises(TelegramAuthenticationError):
                await authentication
