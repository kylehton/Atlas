import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from urllib.parse import quote
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from atlas.db.models import SettingsBrowserSession, SettingsLoginRequest, UserPreference
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.telegram.browser_auth import (
    TelegramAuthenticationError,
    TelegramBrowserAuth,
)
from atlas.telegram.contracts import TELEGRAM_PROVIDER

type Clock = Callable[[], datetime]


class SettingsAccessDenied(PermissionError):
    """Hide whether a settings credential is invalid, expired, used, or revoked."""


class SettingsIdentityMismatch(PermissionError):
    """Reject a Telegram login that differs from the user expected by the settings link."""


class SettingsLoginUnavailable(RuntimeError):
    """Indicate that Telegram browser authentication is not configured."""


class SettingsUserNotFound(LookupError):
    """Indicate that no Telegram identity exists for an Atlas user."""


@dataclass(frozen=True, slots=True)
class IssuedSettingsLoginRequest:
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TelegramLoginStart:
    authorization_url: str


@dataclass(frozen=True, slots=True)
class IssuedSettingsSession:
    value: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SettingsPreferences:
    timezone: str
    quiet_hours_start: time | None
    quiet_hours_end: time | None
    notifications_enabled: bool
    notifications_on_weekends: bool


@dataclass(frozen=True, slots=True)
class _TelegramLoginContext:
    code_verifier: str
    nonce_hash: str


class SettingsService:
    """Require a requested user's verified Telegram identity before opening settings."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        public_base_url: str,
        telegram_auth: TelegramBrowserAuth | None,
        login_request_ttl: timedelta = timedelta(minutes=10),
        session_ttl: timedelta = timedelta(hours=2),
        clock: Clock | None = None,
    ) -> None:
        if login_request_ttl <= timedelta(0) or session_ttl <= timedelta(0):
            raise ValueError("settings credential lifetimes must be positive")
        self._session_factory = session_factory
        self._public_base_url = public_base_url.rstrip("/")
        self._redirect_uri = f"{self._public_base_url}/settings/auth/telegram/callback"
        self._telegram_auth = telegram_auth
        self._login_request_ttl = login_request_ttl
        self._session_ttl = session_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    def issue_login_request(self, user_id: UUID) -> IssuedSettingsLoginRequest:
        """Create a settings link for a trusted caller outside an existing transaction."""

        with session_scope(self._session_factory) as database_session:
            return self.issue_login_request_with_repository(
                AtlasRepository(database_session),
                user_id,
            )

    def issue_login_request_with_repository(
        self,
        repository: AtlasRepository,
        user_id: UUID,
    ) -> IssuedSettingsLoginRequest:
        """Create a settings link using the caller's current database transaction."""

        if self._telegram_auth is None:
            raise SettingsLoginUnavailable
        identity = repository.get_user_external_identity(
            user_id=user_id,
            provider=TELEGRAM_PROVIDER,
        )
        if identity is None:
            raise SettingsUserNotFound(f"user {user_id} has no Telegram identity")

        request_token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._login_request_ttl
        repository.add(
            SettingsLoginRequest(
                user_id=user_id,
                expected_provider_user_id=identity.provider_user_id,
                request_hash=_hash_credential(request_token),
                expires_at=expires_at,
            )
        )
        settings_url = f"{self._public_base_url}/settings#login={quote(request_token)}"
        return IssuedSettingsLoginRequest(url=settings_url, expires_at=expires_at)

    def begin_telegram_login(self, request_token: str) -> TelegramLoginStart:
        """Bind a valid settings link to a fresh OIDC state, nonce, and PKCE verifier."""

        telegram_auth = self._require_telegram_auth()
        now = self._clock()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        with session_scope(self._session_factory) as database_session:
            login_request = AtlasRepository(database_session).get_settings_login_request_for_update(
                _hash_credential(request_token)
            )
            if (
                login_request is None
                or login_request.used_at is not None
                or login_request.expires_at <= now
                or login_request.oidc_state_hash is not None
            ):
                raise SettingsAccessDenied
            login_request.oidc_state_hash = _hash_credential(state)
            login_request.oidc_nonce_hash = _hash_credential(nonce)
            login_request.pkce_verifier = code_verifier

        return TelegramLoginStart(
            authorization_url=telegram_auth.authorization_url(
                redirect_uri=self._redirect_uri,
                state=state,
                nonce=nonce,
                code_challenge=_pkce_challenge(code_verifier),
            )
        )

    async def complete_telegram_login(
        self,
        *,
        code: str,
        state: str,
    ) -> IssuedSettingsSession:
        """Verify Telegram's callback and create a session only for the requested user."""

        telegram_auth = self._require_telegram_auth()
        context = self._get_telegram_login_context(state)
        try:
            verified_identity = await telegram_auth.authenticate(
                code=code,
                redirect_uri=self._redirect_uri,
                code_verifier=context.code_verifier,
                expected_nonce_hash=context.nonce_hash,
            )
        except TelegramAuthenticationError:
            raise SettingsAccessDenied from None

        now = self._clock()
        session_token = secrets.token_urlsafe(32)
        expires_at = now + self._session_ttl
        identity_matches = False
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            login_request = repository.get_settings_login_by_state_for_update(
                _hash_credential(state)
            )
            if (
                login_request is None
                or login_request.used_at is not None
                or login_request.expires_at <= now
                or login_request.pkce_verifier != context.code_verifier
            ):
                raise SettingsAccessDenied

            login_request.used_at = now
            login_request.pkce_verifier = None
            identity_matches = secrets.compare_digest(
                login_request.expected_provider_user_id,
                verified_identity.provider_user_id,
            )
            if identity_matches:
                if repository.get_preferences(login_request.user_id) is None:
                    repository.add(UserPreference(user_id=login_request.user_id))
                repository.add(
                    SettingsBrowserSession(
                        user_id=login_request.user_id,
                        session_hash=_hash_credential(session_token),
                        expires_at=expires_at,
                    )
                )

        if not identity_matches:
            raise SettingsIdentityMismatch
        return IssuedSettingsSession(value=session_token, expires_at=expires_at)

    def get_preferences(self, session_token: str) -> SettingsPreferences:
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            settings_session = self._require_session(repository, session_token)
            preferences = repository.get_preferences(settings_session.user_id)
            if preferences is None:
                raise SettingsAccessDenied
            return _snapshot_preferences(preferences)

    def update_preferences(
        self,
        session_token: str,
        *,
        timezone: str,
        quiet_hours_start: time | None,
        quiet_hours_end: time | None,
        notifications_enabled: bool,
        notifications_on_weekends: bool,
    ) -> SettingsPreferences:
        """Replace settings-managed preferences for the authenticated user."""

        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            settings_session = self._require_session(repository, session_token)
            preferences = repository.get_preferences(settings_session.user_id)
            if preferences is None:
                raise SettingsAccessDenied
            preferences.timezone = timezone
            preferences.quiet_hours_start = quiet_hours_start
            preferences.quiet_hours_end = quiet_hours_end
            preferences.notifications_enabled = notifications_enabled
            preferences.notifications_on_weekends = notifications_on_weekends
            return _snapshot_preferences(preferences)

    def revoke_session(self, session_token: str) -> None:
        """Revoke a known session; unknown credentials remain an idempotent no-op."""

        with session_scope(self._session_factory) as database_session:
            settings_session = AtlasRepository(database_session).get_settings_session(
                _hash_credential(session_token)
            )
            if settings_session is not None and settings_session.revoked_at is None:
                settings_session.revoked_at = self._clock()

    def _get_telegram_login_context(self, state: str) -> _TelegramLoginContext:
        now = self._clock()
        with session_scope(self._session_factory) as database_session:
            login_request = AtlasRepository(
                database_session
            ).get_settings_login_by_state_for_update(_hash_credential(state))
            if (
                login_request is None
                or login_request.used_at is not None
                or login_request.expires_at <= now
                or login_request.pkce_verifier is None
                or login_request.oidc_nonce_hash is None
            ):
                raise SettingsAccessDenied
            return _TelegramLoginContext(
                code_verifier=login_request.pkce_verifier,
                nonce_hash=login_request.oidc_nonce_hash,
            )

    def _require_session(
        self,
        repository: AtlasRepository,
        session_token: str,
    ) -> SettingsBrowserSession:
        settings_session = repository.get_settings_session(_hash_credential(session_token))
        if (
            settings_session is None
            or settings_session.revoked_at is not None
            or settings_session.expires_at <= self._clock()
        ):
            raise SettingsAccessDenied
        return settings_session

    def _require_telegram_auth(self) -> TelegramBrowserAuth:
        if self._telegram_auth is None:
            raise SettingsLoginUnavailable
        return self._telegram_auth


def _hash_credential(value: str) -> str:
    # Random 256-bit credentials do not need password-style slow hashing.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _snapshot_preferences(preferences: UserPreference) -> SettingsPreferences:
    return SettingsPreferences(
        timezone=preferences.timezone,
        quiet_hours_start=preferences.quiet_hours_start,
        quiet_hours_end=preferences.quiet_hours_end,
        notifications_enabled=preferences.notifications_enabled,
        notifications_on_weekends=preferences.notifications_on_weekends,
    )
