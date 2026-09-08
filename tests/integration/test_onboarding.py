import hashlib
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from atlas.api.app import create_app
from atlas.config.config import Settings
from atlas.db.models import (
    ExternalIdentity,
    SettingsBrowserSession,
    SettingsLoginRequest,
    User,
    UserPreference,
)
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.onboarding import SettingsService
from atlas.onboarding.routes import SETTINGS_SESSION_COOKIE
from atlas.telegram.browser_auth import VerifiedTelegramIdentity


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeTelegramBrowserAuth:
    def __init__(self, provider_user_id: str) -> None:
        self.provider_user_id = provider_user_id

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        assert redirect_uri == "https://atlas.test/settings/auth/telegram/callback"
        assert nonce
        assert code_challenge
        return f"https://oauth.telegram.test/auth?state={state}"

    async def authenticate(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce_hash: str,
    ) -> VerifiedTelegramIdentity:
        assert code == "telegram-code"
        assert redirect_uri == "https://atlas.test/settings/auth/telegram/callback"
        assert code_verifier
        assert expected_nonce_hash
        return VerifiedTelegramIdentity(provider_user_id=self.provider_user_id)


def _create_telegram_user(
    session_factory: sessionmaker[Session],
    provider_user_id: str,
) -> User:
    with session_scope(session_factory) as database_session:
        repository = AtlasRepository(database_session)
        user = repository.add(User(display_name="Settings user"))
        repository.add(
            ExternalIdentity(
                user_id=user.id,
                provider="telegram",
                provider_user_id=provider_user_id,
                provider_chat_id=provider_user_id,
                provider_username="settings_user",
            )
        )
        return user


def _request_token(settings_url: str) -> str:
    fragment = parse_qs(urlparse(settings_url).fragment)
    return fragment["login"][0]


async def _start_login(
    client: httpx.AsyncClient,
    request_token: str,
) -> str:
    response = await client.post(
        "/settings/auth/telegram/start",
        json={"request_token": request_token},
    )
    assert response.status_code == 200
    authorization_url = response.json()["authorization_url"]
    return parse_qs(urlparse(authorization_url).query)["state"][0]


@pytest.mark.anyio
async def test_matching_telegram_login_opens_settings_and_saves_preferences(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    provider_user_id = "10001"
    telegram_auth = FakeTelegramBrowserAuth(provider_user_id)
    app = create_app(
        Settings(environment="test", public_base_url="https://atlas.test"),
        postgres_engine,
        telegram_browser_auth=telegram_auth,
    )
    settings_service = cast(SettingsService, app.state.settings_service)
    user = _create_telegram_user(postgres_session_factory, provider_user_id)
    login_request = settings_service.issue_login_request(user.id)
    request_token = _request_token(login_request.url)
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://atlas.test") as client,
    ):
        page = await client.get("/settings")
        assert page.status_code == 200
        assert "User Settings" in page.text
        timezone_options = (await client.get("/settings/timezones")).json()["timezones"]
        assert timezone_options == [
            {"value": "America/Los_Angeles", "label": "Pacific Time (UTC-08:00)"},
            {"value": "America/Chicago", "label": "Central Time (UTC-06:00)"},
            {"value": "America/New_York", "label": "Eastern Time (UTC-05:00)"},
            {"value": "Pacific/Honolulu", "label": "Hawaii Time (UTC-10:00)"},
            {"value": "Asia/Tokyo", "label": "Tokyo (UTC+09:00)"},
            {"value": "Asia/Seoul", "label": "Seoul (UTC+09:00)"},
            {"value": "Asia/Taipei", "label": "Taiwan (UTC+08:00)"},
        ]
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

        state = await _start_login(client, request_token)
        callback = await client.get(
            "/settings/auth/telegram/callback",
            params={"code": "telegram-code", "state": state},
        )
        assert callback.status_code == 303
        cookie_header = callback.headers["set-cookie"]
        assert all(
            attribute in cookie_header
            for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/settings")
        )
        session_token = client.cookies.get(SETTINGS_SESSION_COOKIE)
        assert session_token is not None
        assert (await client.get("/settings/profile")).json() == {
            "display_name": "Settings user",
            "telegram_username": "settings_user",
        }

        update = await client.put(
            "/settings/preferences",
            json={
                "timezone": "America/Los_Angeles",
                "notification_window_start": "09:00:00",
                "notification_window_end": "21:00:00",
                "notifications_enabled": True,
                "notifications_on_weekends": False,
            },
        )
        assert update.status_code == 200
        assert update.json() == {
            "timezone": "America/Los_Angeles",
            "notification_window_start": "09:00:00",
            "notification_window_end": "21:00:00",
            "notifications_enabled": True,
            "notifications_on_weekends": False,
        }
        assert (await client.get("/settings/preferences")).json() == update.json()

        assert (await client.delete("/settings/session")).status_code == 204
        assert (await client.get("/settings/preferences")).status_code == 401
        assert (await client.get("/settings/profile")).status_code == 401

    with session_scope(postgres_session_factory) as database_session:
        stored_request = database_session.scalar(
            select(SettingsLoginRequest).where(SettingsLoginRequest.user_id == user.id)
        )
        stored_session = database_session.scalar(
            select(SettingsBrowserSession).where(SettingsBrowserSession.user_id == user.id)
        )
        preferences = database_session.get(UserPreference, user.id)
        assert stored_request is not None
        assert stored_session is not None
        assert preferences is not None
        assert (
            stored_request.request_hash == hashlib.sha256(request_token.encode("utf-8")).hexdigest()
        )
        assert stored_request.request_hash != request_token
        assert stored_request.used_at is not None
        assert (
            stored_session.session_hash == hashlib.sha256(session_token.encode("utf-8")).hexdigest()
        )
        assert stored_session.session_hash != session_token
        assert stored_session.revoked_at is not None
        assert preferences.timezone == "America/Los_Angeles"


@pytest.mark.anyio
async def test_leaked_link_cannot_open_settings_for_a_different_telegram_user(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    expected_provider_user_id = "20001"
    telegram_auth = FakeTelegramBrowserAuth("99999")
    app = create_app(
        Settings(environment="test", public_base_url="https://atlas.test"),
        postgres_engine,
        telegram_browser_auth=telegram_auth,
    )
    settings_service = cast(SettingsService, app.state.settings_service)
    user = _create_telegram_user(postgres_session_factory, expected_provider_user_id)
    request_token = _request_token(settings_service.issue_login_request(user.id).url)
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://atlas.test") as client,
    ):
        state = await _start_login(client, request_token)
        callback = await client.get(
            "/settings/auth/telegram/callback",
            params={"code": "telegram-code", "state": state},
        )

    assert callback.status_code == 403
    assert SETTINGS_SESSION_COOKIE not in client.cookies
    with session_scope(postgres_session_factory) as database_session:
        session_count = database_session.scalar(
            select(func.count())
            .select_from(SettingsBrowserSession)
            .where(SettingsBrowserSession.user_id == user.id)
        )
        login_request = database_session.scalar(
            select(SettingsLoginRequest).where(SettingsLoginRequest.user_id == user.id)
        )
        assert session_count == 0
        assert login_request is not None
        assert login_request.used_at is not None


@pytest.mark.anyio
async def test_settings_rejects_invalid_expired_and_restarted_login_requests(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    provider_user_id = "30001"
    telegram_auth = FakeTelegramBrowserAuth(provider_user_id)
    app = create_app(
        Settings(environment="test", public_base_url="https://atlas.test"),
        postgres_engine,
        telegram_browser_auth=telegram_auth,
    )
    settings_service = cast(SettingsService, app.state.settings_service)
    user = _create_telegram_user(postgres_session_factory, provider_user_id)
    expired_token = _request_token(settings_service.issue_login_request(user.id).url)
    with session_scope(postgres_session_factory) as database_session:
        stored_request = database_session.scalar(
            select(SettingsLoginRequest).where(
                SettingsLoginRequest.request_hash
                == hashlib.sha256(expired_token.encode("utf-8")).hexdigest()
            )
        )
        assert stored_request is not None
        stored_request.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    valid_token = _request_token(settings_service.issue_login_request(user.id).url)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="https://atlas.test") as client,
    ):
        invalid = await client.post(
            "/settings/auth/telegram/start",
            json={"request_token": "x" * 43},
        )
        expired = await client.post(
            "/settings/auth/telegram/start",
            json={"request_token": expired_token},
        )
        first_start = await client.post(
            "/settings/auth/telegram/start",
            json={"request_token": valid_token},
        )
        restarted = await client.post(
            "/settings/auth/telegram/start",
            json={"request_token": valid_token},
        )

    assert invalid.status_code == 401
    assert expired.status_code == 401
    assert first_start.status_code == 200
    assert restarted.status_code == 401
    assert invalid.json() == expired.json() == restarted.json()
