import base64
import binascii
import hashlib
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from atlas.db.models import (
    GoogleOAuthRequest,
    Integration,
    IntegrationStatus,
    SettingsBrowserSession,
)
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.integrations.google.contracts import (
    GoogleAccessGrant,
    GoogleCapabilityName,
    GoogleOAuth,
    GoogleOAuthError,
    GoogleRefreshRejected,
)
from atlas.integrations.google.security import (
    CredentialCipher,
    CredentialDecryptionError,
    OAuthStateSigner,
    OAuthStateValidationError,
    hash_oauth_state,
)
from atlas.onboarding.service import SettingsService

GOOGLE_PROVIDER = "google"
GOOGLE_WORKSPACE_CAPABILITY = "workspace"
GOOGLE_ACCOUNT_OWNERSHIP_CONSTRAINT = "uq_integrations_google_workspace_account"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
BASE_SCOPES = ("openid", "email")
CAPABILITY_SCOPES: dict[GoogleCapabilityName, str] = {
    "gmail": GMAIL_SCOPE,
    "calendar": CALENDAR_SCOPE,
}

type Clock = Callable[[], datetime]


class GoogleConnectionUnavailable(RuntimeError):
    """Indicate that Atlas does not have complete Google OAuth configuration."""


class GoogleAuthorizationRejected(PermissionError):
    """Reject invalid, expired, replayed, or incomplete Google authorization attempts."""


class GoogleAccountMismatch(PermissionError):
    """Prevent silently replacing an active connection with another Google account."""


class GoogleAccountAlreadyLinked(PermissionError):
    """Prevent one Google grant from being owned by multiple Atlas users."""


class GoogleConnectionDataError(RuntimeError):
    """Indicate that stored Google integrations violate the one-account portal invariant."""


class GoogleCredentialChanged(RuntimeError):
    """Reject a stale lifecycle result after another operation replaces the credential."""


class GoogleConnectionBusy(RuntimeError):
    """Reject credential use while provider-side disconnect is in progress."""


@dataclass(frozen=True, slots=True)
class GoogleAuthorizationStart:
    authorization_url: str
    browser_binding: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GoogleConnectionSnapshot:
    status: IntegrationStatus
    account_email: str | None
    gmail_connected: bool
    calendar_connected: bool


@dataclass(frozen=True, slots=True)
class GoogleDisconnectResult:
    connection: GoogleConnectionSnapshot
    provider_revoked: bool


@dataclass(frozen=True, slots=True)
class _GoogleAuthorizationContext:
    user_id: UUID
    portal_session_id: UUID
    capability: GoogleCapabilityName
    code_verifier: str
    expected_integration_id: UUID | None
    expected_credential_version: int


@dataclass(frozen=True, slots=True)
class _CredentialSnapshot:
    integration_id: UUID | None
    version: int
    encrypted_refresh_token: bytes | None
    encrypted_pending_revocation_tokens: tuple[str, ...]


class GoogleConnectionService:
    """Own Google authorization, encrypted credentials, and connection state transitions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        portal_service: SettingsService,
        *,
        public_base_url: str,
        oauth: GoogleOAuth | None,
        credential_cipher: CredentialCipher | None,
        state_signer: OAuthStateSigner | None,
        oauth_request_ttl: timedelta = timedelta(minutes=10),
        clock: Clock | None = None,
    ) -> None:
        if oauth_request_ttl <= timedelta(0):
            raise ValueError("Google OAuth request lifetime must be positive")
        self._session_factory = session_factory
        self._portal_service = portal_service
        self._redirect_uri = f"{public_base_url.rstrip('/')}/connections/google/callback"
        self._oauth = oauth
        self._credential_cipher = credential_cipher
        self._state_signer = state_signer
        self._oauth_request_ttl = oauth_request_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_connection(self, session_token: str) -> GoogleConnectionSnapshot:
        user_id = self._portal_service.authenticated_user_id(session_token)
        with session_scope(self._session_factory) as database_session:
            integration = self._get_integration(AtlasRepository(database_session), user_id)
            return _snapshot(integration)

    def begin_authorization(
        self,
        session_token: str,
        capability: GoogleCapabilityName,
    ) -> GoogleAuthorizationStart:
        """Create a signed, expiring, one-use state for one incremental capability grant."""

        oauth, _, state_signer = self._require_oauth_components()
        portal_session = self._portal_service.authenticated_session(session_token)
        user_id = portal_session.user_id
        now = self._clock()
        expires_at = now + self._oauth_request_ttl
        request_id = uuid4()
        code_verifier = secrets.token_urlsafe(48)
        browser_binding = secrets.token_urlsafe(32)
        state = state_signer.sign(
            request_id=request_id,
            user_id=user_id,
            expires_at=expires_at,
        )
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            integration = self._get_integration(repository, user_id)
            if integration is not None and integration.status == IntegrationStatus.DISCONNECTING:
                raise GoogleConnectionBusy
            repository.add(
                GoogleOAuthRequest(
                    id=request_id,
                    user_id=user_id,
                    portal_session_id=portal_session.id,
                    state_hash=hash_oauth_state(state),
                    browser_binding_hash=_hash_credential(browser_binding),
                    requested_capabilities=[capability],
                    pkce_verifier=code_verifier,
                    expected_integration_id=(integration.id if integration is not None else None),
                    expected_credential_version=(
                        integration.credential_version if integration is not None else 0
                    ),
                    expires_at=expires_at,
                )
            )

        scopes = (*BASE_SCOPES, CAPABILITY_SCOPES[capability])
        return GoogleAuthorizationStart(
            authorization_url=oauth.authorization_url(
                redirect_uri=self._redirect_uri,
                state=state,
                code_challenge=_pkce_challenge(code_verifier),
                scopes=scopes,
            ),
            browser_binding=browser_binding,
            expires_at=expires_at,
        )

    async def complete_authorization(
        self,
        *,
        state: str,
        code: str,
        browser_binding: str,
    ) -> GoogleConnectionSnapshot:
        """Consume signed state, exchange the code, and store only an encrypted refresh token."""

        oauth, credential_cipher, state_signer = self._require_oauth_components()
        now = self._clock()
        try:
            verified_state = state_signer.verify(state, now=now)
        except OAuthStateValidationError:
            raise GoogleAuthorizationRejected from None
        context = self._consume_authorization_request(
            state=state,
            request_id=verified_state.request_id,
            user_id=verified_state.user_id,
            browser_binding=browser_binding,
            now=now,
        )

        try:
            grant = await oauth.exchange_code(
                code=code,
                redirect_uri=self._redirect_uri,
                code_verifier=context.code_verifier,
            )
        except GoogleOAuthError:
            raise GoogleAuthorizationRejected from None

        persisted = False
        # Until identity proves this grant belongs to an existing Atlas connection,
        # revoke any newly issued refresh token if completion cannot persist it.
        revoke_isolated_grant = grant.refresh_token is not None
        retain_failed_grant = True
        returned_scopes = grant.scopes
        try:
            if returned_scopes is None:
                effective_scopes = frozenset(
                    (*BASE_SCOPES, CAPABILITY_SCOPES[context.capability])
                )
            else:
                if CAPABILITY_SCOPES[context.capability] not in returned_scopes:
                    raise GoogleAuthorizationRejected
                effective_scopes = returned_scopes
            identity = await oauth.get_identity(grant.access_token)
            with session_scope(self._session_factory) as database_session:
                repository = AtlasRepository(database_session)
                if repository.get_user_for_update(context.user_id) is None:
                    raise GoogleAuthorizationRejected
                completed_at = self._clock()
                portal_session = repository.get_settings_session_by_id(
                    context.portal_session_id,
                    for_update=True,
                )
                if not _portal_session_is_active(
                    portal_session,
                    user_id=context.user_id,
                    now=completed_at,
                ):
                    raise GoogleAuthorizationRejected

                account_owner = repository.get_integration_by_provider_account(
                    provider=GOOGLE_PROVIDER,
                    capability=GOOGLE_WORKSPACE_CAPABILITY,
                    account_identifier=identity.subject,
                )
                revoke_isolated_grant = account_owner is None
                if account_owner is not None and account_owner.user_id != context.user_id:
                    raise GoogleAccountAlreadyLinked
                integration = self._get_integration(repository, context.user_id)
                if not _integration_matches_expectation(
                    integration,
                    integration_id=context.expected_integration_id,
                    version=context.expected_credential_version,
                ):
                    raise GoogleCredentialChanged
                if (
                    integration is not None
                    and integration.status != IntegrationStatus.DISCONNECTED
                    and integration.account_identifier != identity.subject
                ):
                    # This callback minted a grant for an unowned account that cannot replace
                    # the user's active connection. Revoke it before discarding the credential.
                    revoke_isolated_grant = account_owner is None
                    retain_failed_grant = False
                    raise GoogleAccountMismatch
                # Cleanup is safe only when this callback began without an existing connection
                # and no Atlas user owns the resolved Google grant.
                revoke_isolated_grant = (
                    context.expected_integration_id is None and account_owner is None
                )

                encrypted_refresh_token = (
                    integration.encrypted_refresh_token if integration is not None else None
                )
                if grant.refresh_token is not None:
                    if integration is not None:
                        _retain_superseded_refresh_token(
                            integration,
                            replacement_token=grant.refresh_token,
                            credential_cipher=credential_cipher,
                        )
                    encrypted_refresh_token = credential_cipher.encrypt(grant.refresh_token)
                if encrypted_refresh_token is None:
                    raise GoogleAuthorizationRejected

                if integration is None:
                    integration = repository.add(
                        Integration(
                            user_id=context.user_id,
                            provider=GOOGLE_PROVIDER,
                            capability=GOOGLE_WORKSPACE_CAPABILITY,
                            account_identifier=identity.subject,
                            credential_version=0,
                        )
                    )
                else:
                    integration.account_identifier = identity.subject

                if returned_scopes is None:
                    effective_scopes |= frozenset(integration.scopes)

                integration.status = IntegrationStatus.CONNECTED
                # An omitted scope means the requested scopes were granted; retain any
                # previously recorded capabilities from incremental authorization.
                integration.scopes = sorted(effective_scopes)
                integration.encrypted_refresh_token = encrypted_refresh_token
                integration.credential_version += 1
                integration.token_metadata = {"account_email": identity.email}
                integration.last_success_at = completed_at
                integration.last_error_at = None
                connection = _snapshot(integration)
            persisted = True
        except GoogleOAuthError:
            raise GoogleAuthorizationRejected from None
        except IntegrityError as error:
            if _is_constraint_violation(error, GOOGLE_ACCOUNT_OWNERSHIP_CONSTRAINT):
                revoke_isolated_grant = False
                raise GoogleAccountAlreadyLinked from None
            raise
        finally:
            if not persisted and revoke_isolated_grant and grant.refresh_token is not None:
                retained_for_cleanup = retain_failed_grant and (
                    self._retain_failed_refresh_token(
                        user_id=context.user_id,
                        integration_id=context.expected_integration_id,
                        refresh_token=grant.refresh_token,
                        credential_cipher=credential_cipher,
                    )
                )
                if not retained_for_cleanup:
                    with suppress(GoogleOAuthError):
                        await oauth.revoke_refresh_token(grant.refresh_token)
        return connection

    def cancel_authorization(self, state: str, *, browser_binding: str) -> None:
        """Validate and consume state when Google returns without an authorization code."""

        _, _, state_signer = self._require_oauth_components()
        now = self._clock()
        try:
            verified_state = state_signer.verify(state, now=now)
        except OAuthStateValidationError:
            raise GoogleAuthorizationRejected from None
        self._consume_authorization_request(
            state=state,
            request_id=verified_state.request_id,
            user_id=verified_state.user_id,
            browser_binding=browser_binding,
            now=now,
        )

    async def refresh_access_token(self, user_id: UUID) -> GoogleAccessGrant:
        """Refresh an access token in memory and update lifecycle health without storing it."""

        oauth, credential_cipher, _ = self._require_oauth_components()
        with session_scope(self._session_factory) as database_session:
            integration = self._get_integration(AtlasRepository(database_session), user_id)
            if integration is not None and integration.status == IntegrationStatus.DISCONNECTING:
                raise GoogleConnectionBusy
            credential = _credential_snapshot(integration)
        if credential.encrypted_refresh_token is None:
            raise GoogleAuthorizationRejected

        try:
            refresh_token = credential_cipher.decrypt(credential.encrypted_refresh_token)
        except CredentialDecryptionError:
            if not self._record_refresh_failure(
                user_id,
                credential,
                IntegrationStatus.REAUTHORIZATION_REQUIRED,
                credential_rejected=False,
            ):
                raise GoogleCredentialChanged from None
            raise GoogleAuthorizationRejected from None

        try:
            grant = await oauth.refresh_access_token(refresh_token)
        except GoogleRefreshRejected:
            if not self._record_refresh_failure(
                user_id,
                credential,
                IntegrationStatus.REAUTHORIZATION_REQUIRED,
                credential_rejected=True,
            ):
                raise GoogleCredentialChanged from None
            raise GoogleAuthorizationRejected from None
        except GoogleOAuthError:
            if not self._record_refresh_failure(
                user_id,
                credential,
                IntegrationStatus.DEGRADED,
            ):
                raise GoogleCredentialChanged from None
            raise

        now = self._clock()
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            if repository.get_user_for_update(user_id) is None:
                raise GoogleCredentialChanged
            integration = self._get_integration(repository, user_id)
            if not _integration_matches_credential(integration, credential):
                raise GoogleCredentialChanged
            assert integration is not None
            if grant.refresh_token is not None:
                _retain_superseded_refresh_token(
                    integration,
                    replacement_token=grant.refresh_token,
                    credential_cipher=credential_cipher,
                )
                integration.encrypted_refresh_token = credential_cipher.encrypt(grant.refresh_token)
                integration.credential_version += 1
            if grant.scopes:
                integration.scopes = sorted(grant.scopes)
            integration.status = IntegrationStatus.CONNECTED
            integration.last_success_at = now
            integration.last_error_at = None
            effective_scopes = frozenset(integration.scopes)
        return GoogleAccessGrant(
            access_token=grant.access_token,
            scopes=effective_scopes,
            expires_at=grant.expires_at,
        )

    async def disconnect(self, session_token: str) -> GoogleDisconnectResult:
        """Block credential replacement before revoking and erasing the Google grant."""

        user_id = self._portal_service.authenticated_user_id(session_token)
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            if repository.get_user_for_update(user_id) is None:
                raise GoogleCredentialChanged
            integration = self._get_integration(repository, user_id)
            already_disconnected = integration is None or (
                integration.status == IntegrationStatus.DISCONNECTED
                and integration.encrypted_refresh_token is None
                and not integration.encrypted_pending_revocation_tokens
            )
            if (
                integration is not None
                and not already_disconnected
                and integration.status != IntegrationStatus.DISCONNECTING
            ):
                integration.status = IntegrationStatus.DISCONNECTING
                integration.credential_version += 1
            credential = _credential_snapshot(integration)

        encrypted_tokens = _all_encrypted_refresh_tokens(credential)
        provider_revoked = not encrypted_tokens
        if encrypted_tokens:
            provider_revoked = await self._revoke_google_grant(encrypted_tokens)

        now = self._clock()
        credential_changed = False
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            if repository.get_user_for_update(user_id) is None:
                raise GoogleCredentialChanged
            integration = self._get_integration(repository, user_id)
            already_disconnected = integration is None or (
                integration.status == IntegrationStatus.DISCONNECTED
                and integration.encrypted_refresh_token is None
                and not integration.encrypted_pending_revocation_tokens
            )
            if already_disconnected:
                connection = _snapshot(integration)
            else:
                assert integration is not None
                if (
                    not _integration_matches_credential(integration, credential)
                    or integration.status != IntegrationStatus.DISCONNECTING
                ):
                    # A bypassed replacement may share the grant that was just revoked.
                    integration.status = IntegrationStatus.REAUTHORIZATION_REQUIRED
                    integration.scopes = []
                    integration.credential_version += 1
                    integration.last_error_at = now
                    connection = _snapshot(integration)
                    credential_changed = True
                else:
                    integration.status = IntegrationStatus.DISCONNECTED
                    integration.scopes = []
                    integration.encrypted_refresh_token = None
                    integration.encrypted_pending_revocation_tokens = []
                    integration.credential_version += 1
                    integration.token_metadata = {}
                    integration.last_error_at = None if provider_revoked else now
                    connection = _snapshot(integration)
        if credential_changed:
            raise GoogleCredentialChanged
        return GoogleDisconnectResult(
            connection=connection,
            provider_revoked=provider_revoked,
        )

    def _consume_authorization_request(
        self,
        *,
        state: str,
        request_id: UUID,
        user_id: UUID,
        browser_binding: str,
        now: datetime,
    ) -> _GoogleAuthorizationContext:
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            request = repository.get_google_oauth_request_for_update(hash_oauth_state(state))
            portal_session = (
                repository.get_settings_session_by_id(request.portal_session_id)
                if request is not None
                else None
            )
            if (
                request is None
                or request.id != request_id
                or request.user_id != user_id
                or not _portal_session_is_active(
                    portal_session,
                    user_id=user_id,
                    now=now,
                )
                or not secrets.compare_digest(
                    request.browser_binding_hash,
                    _hash_credential(browser_binding),
                )
                or request.used_at is not None
                or request.expires_at <= now
                or request.pkce_verifier is None
                or len(request.requested_capabilities) != 1
                or request.requested_capabilities[0] not in CAPABILITY_SCOPES
            ):
                raise GoogleAuthorizationRejected
            capability = request.requested_capabilities[0]
            request.used_at = now
            code_verifier = request.pkce_verifier
            request.pkce_verifier = None
            return _GoogleAuthorizationContext(
                user_id=user_id,
                portal_session_id=request.portal_session_id,
                capability=capability,
                code_verifier=code_verifier,
                expected_integration_id=request.expected_integration_id,
                expected_credential_version=request.expected_credential_version,
            )

    def _record_refresh_failure(
        self,
        user_id: UUID,
        credential: _CredentialSnapshot,
        status: IntegrationStatus,
        *,
        credential_rejected: bool = False,
    ) -> bool:
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            if repository.get_user_for_update(user_id) is None:
                return False
            integration = self._get_integration(repository, user_id)
            if not _integration_matches_credential(integration, credential):
                return False
            if integration is not None:
                integration.status = status
                integration.last_error_at = self._clock()
                if status == IntegrationStatus.REAUTHORIZATION_REQUIRED:
                    integration.scopes = []
                if credential_rejected:
                    integration.encrypted_refresh_token = None
                    integration.credential_version += 1
            return True

    def _retain_failed_refresh_token(
        self,
        *,
        user_id: UUID,
        integration_id: UUID | None,
        refresh_token: str,
        credential_cipher: CredentialCipher,
    ) -> bool:
        """Retain a failed incremental grant without revoking the active Google grant."""

        if integration_id is None:
            return False
        with session_scope(self._session_factory) as database_session:
            repository = AtlasRepository(database_session)
            # Serialize with disconnect's transition to DISCONNECTING so its token
            # snapshot cannot miss a token retained by this failure path.
            if repository.get_user_for_update(user_id) is None:
                return False
            integration = self._get_integration(repository, user_id)
            if (
                integration is None
                or integration.id != integration_id
                or integration.status
                in {IntegrationStatus.DISCONNECTED, IntegrationStatus.DISCONNECTING}
            ):
                return False
            encoded_token = _encode_encrypted_credential(
                credential_cipher.encrypt(refresh_token)
            )
            integration.encrypted_pending_revocation_tokens = [
                *integration.encrypted_pending_revocation_tokens,
                encoded_token,
            ]
            return True

    async def _revoke_google_grant(
        self,
        encrypted_tokens: tuple[bytes, ...],
    ) -> bool:
        """Try retained credentials until Google confirms the combined grant is revoked."""

        if self._oauth is None or self._credential_cipher is None:
            return False
        for encrypted_token in encrypted_tokens:
            try:
                refresh_token = self._credential_cipher.decrypt(encrypted_token)
                await self._oauth.revoke_refresh_token(refresh_token)
            except (CredentialDecryptionError, GoogleOAuthError):
                continue
            # Google revocation invalidates every token in this project's combined grant.
            return True
        return False

    def _get_integration(
        self,
        repository: AtlasRepository,
        user_id: UUID,
    ) -> Integration | None:
        integrations = repository.get_integrations_by_provider_capability(
            user_id=user_id,
            provider=GOOGLE_PROVIDER,
            capability=GOOGLE_WORKSPACE_CAPABILITY,
        )
        if len(integrations) > 1:
            raise GoogleConnectionDataError(
                "a user may have only one Google Workspace connection in the portal"
            )
        return integrations[0] if integrations else None

    def _require_oauth_components(
        self,
    ) -> tuple[GoogleOAuth, CredentialCipher, OAuthStateSigner]:
        if self._oauth is None or self._credential_cipher is None or self._state_signer is None:
            raise GoogleConnectionUnavailable
        return self._oauth, self._credential_cipher, self._state_signer


def _snapshot(integration: Integration | None) -> GoogleConnectionSnapshot:
    if integration is None:
        return GoogleConnectionSnapshot(
            status=IntegrationStatus.DISCONNECTED,
            account_email=None,
            gmail_connected=False,
            calendar_connected=False,
        )
    email = integration.token_metadata.get("account_email")
    usable = integration.status in {
        IntegrationStatus.CONNECTED,
        IntegrationStatus.DEGRADED,
    }
    return GoogleConnectionSnapshot(
        status=integration.status,
        account_email=email if isinstance(email, str) else None,
        gmail_connected=usable and GMAIL_SCOPE in integration.scopes,
        calendar_connected=usable and CALENDAR_SCOPE in integration.scopes,
    )


def _credential_snapshot(integration: Integration | None) -> _CredentialSnapshot:
    if integration is None:
        return _CredentialSnapshot(
            integration_id=None,
            version=0,
            encrypted_refresh_token=None,
            encrypted_pending_revocation_tokens=(),
        )
    return _CredentialSnapshot(
        integration_id=integration.id,
        version=integration.credential_version,
        encrypted_refresh_token=integration.encrypted_refresh_token,
        encrypted_pending_revocation_tokens=tuple(integration.encrypted_pending_revocation_tokens),
    )


def _integration_matches_expectation(
    integration: Integration | None,
    *,
    integration_id: UUID | None,
    version: int,
) -> bool:
    if integration_id is None:
        return integration is None
    return (
        integration is not None
        and integration.id == integration_id
        and integration.credential_version == version
    )


def _integration_matches_credential(
    integration: Integration | None,
    credential: _CredentialSnapshot,
) -> bool:
    if not _integration_matches_expectation(
        integration,
        integration_id=credential.integration_id,
        version=credential.version,
    ):
        return False
    if integration is None:
        return credential.encrypted_refresh_token is None
    return integration.encrypted_refresh_token == credential.encrypted_refresh_token


def _portal_session_is_active(
    portal_session: SettingsBrowserSession | None,
    *,
    user_id: UUID,
    now: datetime,
) -> bool:
    return (
        portal_session is not None
        and portal_session.user_id == user_id
        and portal_session.revoked_at is None
        and portal_session.expires_at > now
    )


def _is_constraint_violation(error: IntegrityError, constraint_name: str) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == constraint_name


def _retain_superseded_refresh_token(
    integration: Integration,
    *,
    replacement_token: str,
    credential_cipher: CredentialCipher,
) -> None:
    encrypted_token = integration.encrypted_refresh_token
    if encrypted_token is None:
        return
    try:
        if secrets.compare_digest(
            credential_cipher.decrypt(encrypted_token),
            replacement_token,
        ):
            return
    except CredentialDecryptionError:
        pass
    encoded_token = _encode_encrypted_credential(encrypted_token)
    if encoded_token not in integration.encrypted_pending_revocation_tokens:
        integration.encrypted_pending_revocation_tokens = [
            *integration.encrypted_pending_revocation_tokens,
            encoded_token,
        ]


def _all_encrypted_refresh_tokens(credential: _CredentialSnapshot) -> tuple[bytes, ...]:
    encrypted_tokens = (
        [credential.encrypted_refresh_token]
        if credential.encrypted_refresh_token is not None
        else []
    )
    for encoded_token in credential.encrypted_pending_revocation_tokens:
        try:
            encrypted_tokens.append(_decode_encrypted_credential(encoded_token))
        except (binascii.Error, ValueError):
            # An invalid placeholder makes the aggregate revocation result unconfirmed.
            encrypted_tokens.append(b"")
    return tuple(encrypted_tokens)


def _encode_encrypted_credential(encrypted_token: bytes) -> str:
    return base64.b64encode(encrypted_token).decode("ascii")


def _decode_encrypted_credential(encoded_token: str) -> bytes:
    return base64.b64decode(encoded_token, validate=True)


def _hash_credential(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
