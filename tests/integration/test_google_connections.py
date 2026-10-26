import asyncio
import base64
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from atlas.api.app import create_app
from atlas.config.config import Settings
from atlas.connections.routes import (
    GOOGLE_OAUTH_BINDING_COOKIE,
    GOOGLE_OAUTH_CALLBACK_PATH,
)
from atlas.db.models import (
    ExternalIdentity,
    GoogleOAuthRequest,
    Integration,
    IntegrationStatus,
    SettingsBrowserSession,
    User,
)
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.integrations.google.contracts import (
    GoogleIdentity,
    GoogleOAuthError,
    GoogleRefreshRejected,
    GoogleTokenGrant,
)
from atlas.integrations.google.security import CredentialCipher
from atlas.integrations.google.service import (
    CALENDAR_SCOPE,
    GMAIL_SCOPE,
    GoogleAccountAlreadyLinked,
    GoogleAccountMismatch,
    GoogleAuthorizationRejected,
    GoogleConnectionBusy,
    GoogleConnectionService,
    GoogleCredentialChanged,
)
from atlas.onboarding.auth_routes import PORTAL_SESSION_COOKIE


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeGoogleOAuth:
    def __init__(self, *, subject: str) -> None:
        self.subject = subject
        self.authorization_scopes: list[tuple[str, ...]] = []
        self.exchange_count = 0
        self.revocation_attempts: list[str] = []
        self.revocation_failures: set[str] = set()
        self.revoked_tokens: list[str] = []
        self.refresh_failure: str | None = None
        self.forced_refresh_token: str | None = None
        self.identity_failure = False
        self.omit_scope_on_exchanges: set[int] = set()

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        assert redirect_uri == "https://atlas.test/connections/google/callback"
        assert code_challenge
        self.authorization_scopes.append(scopes)
        return f"https://accounts.google.test/authorize?state={state}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> GoogleTokenGrant:
        assert code in {"gmail-code", "calendar-code"}
        assert redirect_uri == "https://atlas.test/connections/google/callback"
        assert code_verifier
        self.exchange_count += 1
        scopes = {GMAIL_SCOPE}
        if self.exchange_count in {2, 4}:
            scopes.add(CALENDAR_SCOPE)
        refresh_token = self.forced_refresh_token or {
            1: "refresh-secret",
            3: "replacement-refresh",
        }.get(self.exchange_count)
        return GoogleTokenGrant(
            access_token=f"temporary-access-{self.exchange_count}",
            refresh_token=refresh_token,
            scopes=(
                None
                if self.exchange_count in self.omit_scope_on_exchanges
                else frozenset(scopes)
            ),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def get_identity(self, access_token: str) -> GoogleIdentity:
        assert access_token.startswith("temporary-access-")
        if self.identity_failure:
            raise GoogleOAuthError
        return GoogleIdentity(subject=self.subject, email="atlas@example.com")

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenGrant:
        assert refresh_token in {"refresh-secret", "replacement-refresh"}
        if self.refresh_failure == "rejected":
            raise GoogleRefreshRejected
        if self.refresh_failure == "transient":
            raise GoogleOAuthError
        return GoogleTokenGrant(
            access_token="refreshed-access",
            refresh_token=None,
            scopes=frozenset({GMAIL_SCOPE, CALENDAR_SCOPE}),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        self.revocation_attempts.append(refresh_token)
        if refresh_token in self.revocation_failures:
            raise GoogleOAuthError
        self.revoked_tokens.append(refresh_token)


def _create_portal_session(
    session_factory: sessionmaker[Session],
    session_token: str,
    *,
    provider_user_id: str = "90001",
) -> User:
    with session_scope(session_factory) as database_session:
        repository = AtlasRepository(database_session)
        user = repository.add(User(display_name="Connection user"))
        repository.add(
            ExternalIdentity(
                user_id=user.id,
                provider="telegram",
                provider_user_id=provider_user_id,
                provider_chat_id=provider_user_id,
                provider_username="connection_user",
            )
        )
        repository.add(
            SettingsBrowserSession(
                user_id=user.id,
                session_hash=hashlib.sha256(session_token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        return user


async def _authorize(
    client: httpx.AsyncClient,
    *,
    capability: str,
    code: str,
) -> tuple[str, httpx.Response]:
    start = await client.post(
        "/connections/google/authorize",
        json={"capability": capability},
    )
    assert start.status_code == 200
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    callback = await client.get(
        "/connections/google/callback",
        params={"state": state, "code": code},
    )
    return state, callback


@pytest.mark.anyio
async def test_google_connection_is_incremental_encrypted_refreshable_and_revocable(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="main-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "portal-session-token"
    user = _create_portal_session(postgres_session_factory, session_token)
    attacker_session_token = "attacker-portal-session"
    _create_portal_session(
        postgres_session_factory,
        attacker_session_token,
        provider_user_id="90003",
    )
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://atlas.test") as client,
    ):
        client.cookies.set(PORTAL_SESSION_COOKIE, session_token)
        page = await client.get("/connections")
        assert page.status_code == 200
        assert "Google Workspace" in page.text

        cancelled_start = await client.post(
            "/connections/google/authorize",
            json={"capability": "gmail"},
        )
        cancelled_state = parse_qs(urlparse(cancelled_start.json()["authorization_url"]).query)[
            "state"
        ][0]
        cancelled = await client.get(
            "/connections/google/callback",
            params={"state": cancelled_state, "error": "access_denied"},
        )
        assert cancelled.status_code == 303
        assert cancelled.headers["location"] == "/connections?google=cancelled"
        assert google_oauth.exchange_count == 0

        gmail_start = await client.post(
            "/connections/google/authorize",
            json={"capability": "gmail"},
        )
        assert gmail_start.status_code == 200
        binding_cookie = gmail_start.headers["set-cookie"]
        assert f"{GOOGLE_OAUTH_BINDING_COOKIE}=" in binding_cookie
        assert f"Path={GOOGLE_OAUTH_CALLBACK_PATH}" in binding_cookie
        assert "HttpOnly" in binding_cookie
        assert "SameSite=lax" in binding_cookie
        assert "Secure" in binding_cookie
        gmail_browser_binding = client.cookies.get(GOOGLE_OAUTH_BINDING_COOKIE)
        assert gmail_browser_binding is not None
        gmail_state = parse_qs(urlparse(gmail_start.json()["authorization_url"]).query)["state"][0]

        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://atlas.test",
        ) as attacker:
            attacker.cookies.set(PORTAL_SESSION_COOKIE, attacker_session_token)
            stolen_callback = await attacker.get(
                "/connections/google/callback",
                params={"state": gmail_state, "code": "gmail-code"},
            )
        assert stolen_callback.status_code == 400
        assert google_oauth.exchange_count == 0

        gmail_callback = await client.get(
            "/connections/google/callback",
            params={"state": gmail_state, "code": "gmail-code"},
        )
        assert gmail_callback.status_code == 303
        assert gmail_callback.headers["location"] == "/connections?google=connected"
        assert GMAIL_SCOPE in google_oauth.authorization_scopes[-1]

        client.cookies.set(
            GOOGLE_OAUTH_BINDING_COOKIE,
            gmail_browser_binding,
            path=GOOGLE_OAUTH_CALLBACK_PATH,
        )
        tampered_state = f"{gmail_state[:-1]}{'a' if gmail_state[-1] != 'a' else 'b'}"
        tampered = await client.get(
            "/connections/google/callback",
            params={"state": tampered_state, "code": "gmail-code"},
        )
        assert tampered.status_code == 400

        client.cookies.set(
            GOOGLE_OAUTH_BINDING_COOKIE,
            gmail_browser_binding,
            path=GOOGLE_OAUTH_CALLBACK_PATH,
        )
        replay = await client.get(
            "/connections/google/callback",
            params={"state": gmail_state, "code": "gmail-code"},
        )
        assert replay.status_code == 400

        google_oauth.omit_scope_on_exchanges.add(2)
        _, calendar_callback = await _authorize(
            client,
            capability="calendar",
            code="calendar-code",
        )
        assert calendar_callback.status_code == 303
        assert CALENDAR_SCOPE in google_oauth.authorization_scopes[-1]

        connection = (await client.get("/connections/google")).json()
        assert connection == {
            "status": "connected",
            "account_email": "atlas@example.com",
            "gmail_connected": True,
            "calendar_connected": True,
        }

        with session_scope(postgres_session_factory) as database_session:
            connected_integration = database_session.scalar(
                select(Integration).where(Integration.user_id == user.id)
            )
            assert connected_integration is not None
            assert connected_integration.encrypted_refresh_token is not None
            assert connected_integration.encrypted_refresh_token != b"refresh-secret"
            assert (
                CredentialCipher(credential_key).decrypt(
                    connected_integration.encrypted_refresh_token
                )
                == "refresh-secret"
            )

        service: GoogleConnectionService = app.state.google_connection_service
        google_oauth.refresh_failure = "transient"
        with pytest.raises(GoogleOAuthError):
            await service.refresh_access_token(user.id)
        assert (await client.get("/connections/google")).json()["status"] == "degraded"

        google_oauth.refresh_failure = "rejected"
        with pytest.raises(GoogleAuthorizationRejected):
            await service.refresh_access_token(user.id)
        reauthorization_status = (await client.get("/connections/google")).json()
        assert reauthorization_status["status"] == "reauthorization_required"
        assert reauthorization_status["gmail_connected"] is False
        assert reauthorization_status["calendar_connected"] is False

        _, gmail_reauthorization = await _authorize(
            client,
            capability="gmail",
            code="gmail-code",
        )
        assert gmail_reauthorization.status_code == 303
        gmail_only_status = (await client.get("/connections/google")).json()
        assert gmail_only_status["status"] == "connected"
        assert gmail_only_status["gmail_connected"] is True
        assert gmail_only_status["calendar_connected"] is False

        _, calendar_reauthorization = await _authorize(
            client,
            capability="calendar",
            code="calendar-code",
        )
        assert calendar_reauthorization.status_code == 303
        restored_status = (await client.get("/connections/google")).json()
        assert restored_status["gmail_connected"] is True
        assert restored_status["calendar_connected"] is True

        google_oauth.refresh_failure = None
        refreshed = await service.refresh_access_token(user.id)
        assert refreshed.access_token == "refreshed-access"
        assert (await client.get("/connections/google")).json()["status"] == "connected"

        disconnect = await client.delete("/connections/google")
        assert disconnect.status_code == 200
        assert disconnect.json()["provider_revoked"] is True
        assert disconnect.json()["connection"]["status"] == "disconnected"
        assert google_oauth.revoked_tokens == ["replacement-refresh"]

        google_oauth.forced_refresh_token = "abandoned-refresh"
        google_oauth.identity_failure = True
        _, failed_callback = await _authorize(
            client,
            capability="gmail",
            code="gmail-code",
        )
        assert failed_callback.status_code == 400
        assert google_oauth.revoked_tokens == [
            "replacement-refresh",
            "abandoned-refresh",
        ]

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        oauth_requests = tuple(
            database_session.scalars(
                select(GoogleOAuthRequest).where(GoogleOAuthRequest.user_id == user.id)
            )
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.DISCONNECTED
        assert integration.encrypted_refresh_token is None
        assert integration.scopes == []
        assert integration.token_metadata == {}
        assert len(oauth_requests) == 6
        assert all(request.used_at is not None for request in oauth_requests)


@pytest.mark.anyio
async def test_superseded_refresh_token_is_retained_as_disconnect_fallback(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="superseded-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "superseded-token-session"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90004",
    )
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://atlas.test") as client,
    ):
        client.cookies.set(PORTAL_SESSION_COOKIE, session_token)
        _, first_callback = await _authorize(
            client,
            capability="gmail",
            code="gmail-code",
        )
        assert first_callback.status_code == 303

        google_oauth.forced_refresh_token = "replacement-refresh"
        _, replacement_callback = await _authorize(
            client,
            capability="calendar",
            code="calendar-code",
        )
        assert replacement_callback.status_code == 303
        assert google_oauth.revocation_attempts == []

        with session_scope(postgres_session_factory) as database_session:
            integration = database_session.scalar(
                select(Integration).where(Integration.user_id == user.id)
            )
            assert integration is not None
            assert integration.encrypted_refresh_token is not None
            assert (
                CredentialCipher(credential_key).decrypt(integration.encrypted_refresh_token)
                == "replacement-refresh"
            )
            assert len(integration.encrypted_pending_revocation_tokens) == 1
            assert (
                CredentialCipher(credential_key).decrypt(
                    base64.b64decode(
                        integration.encrypted_pending_revocation_tokens[0],
                        validate=True,
                    )
                )
                == "refresh-secret"
            )

        google_oauth.revocation_failures.add("replacement-refresh")
        disconnect = await client.delete("/connections/google")
        assert disconnect.status_code == 200
        assert disconnect.json()["provider_revoked"] is True

    assert google_oauth.revocation_attempts == ["replacement-refresh", "refresh-secret"]
    assert google_oauth.revoked_tokens == ["refresh-secret"]
    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.encrypted_refresh_token is None
        assert integration.encrypted_pending_revocation_tokens == []


@pytest.mark.anyio
async def test_decryption_failure_preserves_unconfirmed_token_until_disconnect(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    stored_credential_key = Fernet.generate_key().decode()
    active_credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="decryption-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=active_credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "unreadable-token-session"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90005",
    )
    _create_connected_google_integration(
        postgres_session_factory,
        user=user,
        credential_key=stored_credential_key,
    )
    service: GoogleConnectionService = app.state.google_connection_service

    with pytest.raises(GoogleAuthorizationRejected):
        await service.refresh_access_token(user.id)

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.REAUTHORIZATION_REQUIRED
        assert integration.encrypted_refresh_token is not None
        assert integration.scopes == []

    disconnect = await service.disconnect(session_token)
    assert disconnect.provider_revoked is False
    assert google_oauth.revocation_attempts == []

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.DISCONNECTED
        assert integration.encrypted_refresh_token is None


@pytest.mark.anyio
async def test_failed_incremental_authorization_does_not_revoke_existing_grant(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="incremental-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "incremental-failure-session"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90006",
    )
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://atlas.test") as client,
    ):
        client.cookies.set(PORTAL_SESSION_COOKIE, session_token)
        _, first_callback = await _authorize(
            client,
            capability="gmail",
            code="gmail-code",
        )
        assert first_callback.status_code == 303

        google_oauth.forced_refresh_token = "incremental-refresh"
        google_oauth.identity_failure = True
        _, failed_callback = await _authorize(
            client,
            capability="calendar",
            code="calendar-code",
        )
        assert failed_callback.status_code == 400
        assert google_oauth.revocation_attempts == []

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.CONNECTED
        assert integration.encrypted_refresh_token is not None
        assert (
            CredentialCipher(credential_key).decrypt(integration.encrypted_refresh_token)
            == "refresh-secret"
        )
        assert len(integration.encrypted_pending_revocation_tokens) == 1
        assert (
            CredentialCipher(credential_key).decrypt(
                base64.b64decode(
                    integration.encrypted_pending_revocation_tokens[0],
                    validate=True,
                )
            )
            == "incremental-refresh"
        )


@pytest.mark.anyio
async def test_google_account_cannot_be_linked_to_two_atlas_users(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="shared-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    first_session_token = "first-google-owner"
    second_session_token = "second-google-owner"
    first_user = _create_portal_session(
        postgres_session_factory,
        first_session_token,
        provider_user_id="90007",
    )
    second_user = _create_portal_session(
        postgres_session_factory,
        second_session_token,
        provider_user_id="90008",
    )
    service: GoogleConnectionService = app.state.google_connection_service

    first_authorization = service.begin_authorization(first_session_token, "gmail")
    first_state = parse_qs(urlparse(first_authorization.authorization_url).query)["state"][0]
    await service.complete_authorization(
        state=first_state,
        code="gmail-code",
        browser_binding=first_authorization.browser_binding,
    )

    google_oauth.forced_refresh_token = "duplicate-refresh"
    second_authorization = service.begin_authorization(second_session_token, "gmail")
    second_state = parse_qs(urlparse(second_authorization.authorization_url).query)["state"][0]
    with pytest.raises(GoogleAccountAlreadyLinked):
        await service.complete_authorization(
            state=second_state,
            code="gmail-code",
            browser_binding=second_authorization.browser_binding,
        )

    with session_scope(postgres_session_factory) as database_session:
        integrations = tuple(
            database_session.scalars(
                select(Integration).where(Integration.account_identifier == "shared-google-subject")
            )
        )
        assert len(integrations) == 1
        assert integrations[0].user_id == first_user.id
        assert integrations[0].user_id != second_user.id
    assert google_oauth.revocation_attempts == []


@pytest.mark.anyio
async def test_disconnected_google_account_can_be_linked_to_another_atlas_user(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="transferred-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    first_session_token = "former-google-owner"
    second_session_token = "new-google-owner"
    first_user = _create_portal_session(
        postgres_session_factory,
        first_session_token,
        provider_user_id="90010",
    )
    second_user = _create_portal_session(
        postgres_session_factory,
        second_session_token,
        provider_user_id="90011",
    )
    service: GoogleConnectionService = app.state.google_connection_service

    first_authorization = service.begin_authorization(first_session_token, "gmail")
    first_state = parse_qs(urlparse(first_authorization.authorization_url).query)["state"][0]
    await service.complete_authorization(
        state=first_state,
        code="gmail-code",
        browser_binding=first_authorization.browser_binding,
    )
    await service.disconnect(first_session_token)

    google_oauth.forced_refresh_token = "new-owner-refresh"
    second_authorization = service.begin_authorization(second_session_token, "gmail")
    second_state = parse_qs(urlparse(second_authorization.authorization_url).query)["state"][0]
    connection = await service.complete_authorization(
        state=second_state,
        code="gmail-code",
        browser_binding=second_authorization.browser_binding,
    )

    assert connection.status == IntegrationStatus.CONNECTED
    with session_scope(postgres_session_factory) as database_session:
        integrations = {
            integration.user_id: integration
            for integration in database_session.scalars(
                select(Integration).where(
                    Integration.account_identifier == "transferred-google-subject"
                )
            )
        }
        assert len(integrations) == 2
        assert integrations[first_user.id].status == IntegrationStatus.DISCONNECTED
        assert integrations[second_user.id].status == IntegrationStatus.CONNECTED
    assert google_oauth.revocation_attempts == ["refresh-secret"]


@pytest.mark.anyio
async def test_mismatched_google_account_grant_is_revoked(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="original-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "mismatched-google-account"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90012",
    )
    service: GoogleConnectionService = app.state.google_connection_service

    original_authorization = service.begin_authorization(session_token, "gmail")
    original_state = parse_qs(urlparse(original_authorization.authorization_url).query)["state"][0]
    await service.complete_authorization(
        state=original_state,
        code="gmail-code",
        browser_binding=original_authorization.browser_binding,
    )

    google_oauth.subject = "mismatched-google-subject"
    google_oauth.forced_refresh_token = "mismatched-refresh"
    mismatched_authorization = service.begin_authorization(session_token, "gmail")
    mismatched_state = parse_qs(urlparse(mismatched_authorization.authorization_url).query)[
        "state"
    ][0]
    with pytest.raises(GoogleAccountMismatch):
        await service.complete_authorization(
            state=mismatched_state,
            code="gmail-code",
            browser_binding=mismatched_authorization.browser_binding,
        )

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.account_identifier == "original-google-subject"
        assert integration.status == IntegrationStatus.CONNECTED
    assert google_oauth.revocation_attempts == ["mismatched-refresh"]
    assert google_oauth.revoked_tokens == ["mismatched-refresh"]


@pytest.mark.anyio
async def test_google_callback_rejects_a_revoked_initiating_portal_session(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = FakeGoogleOAuth(subject="revoked-google-subject")
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "revoked-oauth-session"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90009",
    )
    service: GoogleConnectionService = app.state.google_connection_service
    authorization = service.begin_authorization(session_token, "gmail")
    state = parse_qs(urlparse(authorization.authorization_url).query)["state"][0]

    app.state.settings_service.revoke_session(session_token)
    with pytest.raises(GoogleAuthorizationRejected):
        await service.complete_authorization(
            state=state,
            code="gmail-code",
            browser_binding=authorization.browser_binding,
        )

    assert google_oauth.exchange_count == 0
    with session_scope(postgres_session_factory) as database_session:
        assert (
            database_session.scalar(select(Integration).where(Integration.user_id == user.id))
            is None
        )


class ConcurrentGoogleOAuth:
    """Release two authorization exchanges together so database serialization is exercised."""

    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.revoked_tokens: list[str] = []

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        return f"https://accounts.google.test/authorize?state={state}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> GoogleTokenGrant:
        self.barrier.wait(timeout=5)
        return GoogleTokenGrant(
            access_token=f"access-{code}",
            refresh_token=f"refresh-{code}",
            scopes=frozenset({GMAIL_SCOPE}),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def get_identity(self, access_token: str) -> GoogleIdentity:
        account = access_token.removeprefix("access-")
        return GoogleIdentity(subject=f"subject-{account}", email=f"{account}@example.com")

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenGrant:
        raise AssertionError("refresh is not part of this test")

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        self.revoked_tokens.append(refresh_token)


class DelayedLifecycleGoogleOAuth:
    """Pause credential lifecycle calls while a replacement is committed."""

    def __init__(
        self,
        *,
        reject_refresh: bool = False,
        rotate_refresh: bool = False,
        block_revocation: bool = False,
    ) -> None:
        self.reject_refresh = reject_refresh
        self.rotate_refresh = rotate_refresh
        self.block_revocation = block_revocation
        self.refresh_started = asyncio.Event()
        self.release_refresh = asyncio.Event()
        self.revoke_started = asyncio.Event()
        self.release_revoke = asyncio.Event()
        self.revoked_tokens: list[str] = []

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scopes: tuple[str, ...],
    ) -> str:
        raise AssertionError("authorization is not part of this test")

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> GoogleTokenGrant:
        raise AssertionError("authorization is not part of this test")

    async def get_identity(self, access_token: str) -> GoogleIdentity:
        raise AssertionError("authorization is not part of this test")

    async def refresh_access_token(self, refresh_token: str) -> GoogleTokenGrant:
        assert refresh_token == "old-refresh"
        self.refresh_started.set()
        await self.release_refresh.wait()
        if self.reject_refresh:
            raise GoogleRefreshRejected
        return GoogleTokenGrant(
            access_token="stale-access",
            refresh_token="rotated-refresh" if self.rotate_refresh else None,
            scopes=frozenset({GMAIL_SCOPE}),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    async def revoke_refresh_token(self, refresh_token: str) -> None:
        self.revoked_tokens.append(refresh_token)
        self.revoke_started.set()
        if self.block_revocation:
            await self.release_revoke.wait()


def _create_connected_google_integration(
    session_factory: sessionmaker[Session],
    *,
    user: User,
    credential_key: str,
) -> None:
    with session_scope(session_factory) as database_session:
        AtlasRepository(database_session).add(
            Integration(
                user_id=user.id,
                provider="google",
                capability="workspace",
                account_identifier=f"google-subject-{user.id}",
                status=IntegrationStatus.CONNECTED,
                scopes=[GMAIL_SCOPE],
                encrypted_refresh_token=CredentialCipher(credential_key).encrypt("old-refresh"),
                credential_version=1,
                token_metadata={"account_email": "old@example.com"},
            )
        )


def _replace_google_credential(
    session_factory: sessionmaker[Session],
    *,
    user: User,
    credential_key: str,
    refresh_token: str,
) -> None:
    with session_scope(session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        integration.encrypted_refresh_token = CredentialCipher(credential_key).encrypt(
            refresh_token
        )
        integration.credential_version += 1
        integration.status = IntegrationStatus.CONNECTED


@pytest.mark.anyio
@pytest.mark.parametrize("reject_refresh", [False, True])
async def test_refresh_does_not_modify_a_replaced_credential(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
    reject_refresh: bool,
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = DelayedLifecycleGoogleOAuth(
        reject_refresh=reject_refresh,
        rotate_refresh=not reject_refresh,
    )
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    user = _create_portal_session(
        postgres_session_factory,
        f"refresh-race-{reject_refresh}",
        provider_user_id=f"9010{int(reject_refresh)}",
    )
    _create_connected_google_integration(
        postgres_session_factory,
        user=user,
        credential_key=credential_key,
    )
    service: GoogleConnectionService = app.state.google_connection_service

    refresh = asyncio.create_task(service.refresh_access_token(user.id))
    await asyncio.wait_for(google_oauth.refresh_started.wait(), timeout=5)
    _replace_google_credential(
        postgres_session_factory,
        user=user,
        credential_key=credential_key,
        refresh_token="replacement-refresh",
    )
    google_oauth.release_refresh.set()

    with pytest.raises(GoogleCredentialChanged):
        await refresh

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.CONNECTED
        assert integration.credential_version == 2
        assert integration.encrypted_refresh_token is not None
        assert (
            CredentialCipher(credential_key).decrypt(integration.encrypted_refresh_token)
            == "replacement-refresh"
        )
    assert google_oauth.revoked_tokens == []


@pytest.mark.anyio
async def test_disconnect_does_not_erase_a_concurrent_reauthorization(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = DelayedLifecycleGoogleOAuth(block_revocation=True)
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "disconnect-race"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90102",
    )
    _create_connected_google_integration(
        postgres_session_factory,
        user=user,
        credential_key=credential_key,
    )
    service: GoogleConnectionService = app.state.google_connection_service

    disconnect = asyncio.create_task(service.disconnect(session_token))
    await asyncio.wait_for(google_oauth.revoke_started.wait(), timeout=5)
    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.DISCONNECTING
        integration_id = integration.id

    assert service._retain_failed_refresh_token(
        user_id=user.id,
        integration_id=integration_id,
        refresh_token="late-refresh",
        credential_cipher=CredentialCipher(credential_key),
    ) is False

    with pytest.raises(GoogleConnectionBusy):
        service.begin_authorization(session_token, "gmail")
    with pytest.raises(GoogleConnectionBusy):
        await service.refresh_access_token(user.id)

    # Simulate a future code path bypassing the lifecycle guard while revocation is in flight.
    _replace_google_credential(
        postgres_session_factory,
        user=user,
        credential_key=credential_key,
        refresh_token="replacement-refresh",
    )
    google_oauth.release_revoke.set()

    with pytest.raises(GoogleCredentialChanged):
        await disconnect

    with session_scope(postgres_session_factory) as database_session:
        integration = database_session.scalar(
            select(Integration).where(Integration.user_id == user.id)
        )
        assert integration is not None
        assert integration.status == IntegrationStatus.REAUTHORIZATION_REQUIRED
        assert integration.credential_version == 4
        assert integration.scopes == []
        assert integration.encrypted_refresh_token is not None
        assert (
            CredentialCipher(credential_key).decrypt(integration.encrypted_refresh_token)
            == "replacement-refresh"
        )
    assert google_oauth.revoked_tokens == ["old-refresh"]


@pytest.mark.anyio
async def test_concurrent_first_callbacks_create_only_one_google_workspace_connection(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    credential_key = Fernet.generate_key().decode()
    google_oauth = ConcurrentGoogleOAuth()
    app = create_app(
        Settings(
            _env_file=None,
            environment="test",
            public_base_url="https://atlas.test",
            credential_encryption_key=credential_key,
        ),
        postgres_engine,
        google_oauth=google_oauth,
    )
    session_token = "concurrent-portal-session"
    user = _create_portal_session(
        postgres_session_factory,
        session_token,
        provider_user_id="90002",
    )
    service: GoogleConnectionService = app.state.google_connection_service
    authorizations = [service.begin_authorization(session_token, "gmail") for _ in range(2)]
    states = [
        parse_qs(urlparse(authorization.authorization_url).query)["state"][0]
        for authorization in authorizations
    ]

    def complete(state: str, code: str, browser_binding: str) -> str:
        try:
            asyncio.run(
                service.complete_authorization(
                    state=state,
                    code=code,
                    browser_binding=browser_binding,
                )
            )
        except (GoogleAccountMismatch, GoogleCredentialChanged):
            return "mismatch"
        return "connected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                lambda attempt: complete(*attempt),
                zip(
                    states,
                    ("account-a", "account-b"),
                    (authorization.browser_binding for authorization in authorizations),
                    strict=True,
                ),
            )
        )

    with session_scope(postgres_session_factory) as database_session:
        integrations = tuple(
            database_session.scalars(select(Integration).where(Integration.user_id == user.id))
        )
    assert sorted(results) == ["connected", "mismatch"]
    assert len(integrations) == 1
    connected_account = integrations[0].account_identifier.removeprefix("subject-")
    rejected_account = ({"account-a", "account-b"} - {connected_account}).pop()
    assert google_oauth.revoked_tokens == [f"refresh-{rejected_account}"]
