from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from atlas.integrations.google.client import (
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    GoogleOAuthClient,
)
from atlas.integrations.google.contracts import GoogleRefreshRejected


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_google_oauth_client_authorizes_exchanges_refreshes_and_revokes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == GOOGLE_USERINFO_URL:
            assert request.headers["Authorization"] == "Bearer temporary-access"
            return httpx.Response(
                200,
                json={
                    "sub": "google-subject",
                    "email": "atlas@example.com",
                    "email_verified": True,
                },
            )
        if str(request.url) == GOOGLE_REVOKE_URL:
            assert parse_qs(request.content.decode())["token"] == ["refresh-secret"]
            return httpx.Response(200)
        assert str(request.url) == GOOGLE_TOKEN_URL
        form = parse_qs(request.content.decode())
        if form["grant_type"] == ["refresh_token"]:
            return httpx.Response(
                200,
                json={
                    "access_token": "refreshed-access",
                    "expires_in": 3600,
                    "scope": "calendar-scope gmail-scope",
                },
            )
        assert form["code_verifier"] == ["verifier"]
        return httpx.Response(
            200,
            json={
                "access_token": "temporary-access",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "scope": "gmail-scope",
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    oauth = GoogleOAuthClient(
        "client-id",
        SecretStr("client-secret"),
        client=http_client,
        clock=lambda: datetime(2026, 9, 8, tzinfo=UTC),
    )
    authorization_url = oauth.authorization_url(
        redirect_uri="https://atlas.test/connections/google/callback",
        state="signed-state",
        code_challenge="challenge",
        scopes=("openid", "email", "gmail-scope"),
    )
    parameters = parse_qs(urlparse(authorization_url).query)
    assert parameters["access_type"] == ["offline"]
    assert parameters["include_granted_scopes"] == ["true"]
    assert parameters["code_challenge_method"] == ["S256"]

    grant = await oauth.exchange_code(
        code="authorization-code",
        redirect_uri="https://atlas.test/connections/google/callback",
        code_verifier="verifier",
    )
    identity = await oauth.get_identity(grant.access_token)
    refreshed = await oauth.refresh_access_token(grant.refresh_token or "")
    await oauth.revoke_refresh_token(grant.refresh_token or "")
    await http_client.aclose()

    assert grant.refresh_token == "refresh-secret"
    assert identity.email == "atlas@example.com"
    assert refreshed.access_token == "refreshed-access"
    assert len(requests) == 4


@pytest.mark.anyio
async def test_google_oauth_client_identifies_rejected_refresh_token() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        oauth = GoogleOAuthClient(
            "client-id",
            SecretStr("client-secret"),
            client=http_client,
        )
        with pytest.raises(GoogleRefreshRejected):
            await oauth.refresh_access_token("expired-refresh-token")


@pytest.mark.anyio
async def test_google_oauth_client_preserves_omitted_scope() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "temporary-access",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        oauth = GoogleOAuthClient(
            "client-id",
            SecretStr("client-secret"),
            client=http_client,
        )
        grant = await oauth.exchange_code(
            code="authorization-code",
            redirect_uri="https://atlas.test/connections/google/callback",
            code_verifier="verifier",
        )

    assert grant.scopes is None
