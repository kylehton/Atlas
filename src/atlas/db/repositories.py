from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from atlas.db.base import Base
from atlas.db.models import (
    ExternalIdentity,
    Integration,
    ProcessedTelegramUpdate,
    SettingsBrowserSession,
    SettingsLoginRequest,
    User,
    UserPreference,
    Workflow,
)


class AtlasRepository:
    """Persist core records and scope every user-owned lookup to its owner."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add[T: Base](self, record: T) -> T:
        """Stage a record and flush so generated IDs are immediately available."""

        self._session.add(record)
        self._session.flush()
        return record

    def get_user(self, user_id: UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_preferences(self, user_id: UUID) -> UserPreference | None:
        return self._session.get(UserPreference, user_id)

    def get_settings_login_request_for_update(
        self,
        request_hash: str,
    ) -> SettingsLoginRequest | None:
        """Lock a settings-link request while its Telegram login is started."""

        return self._session.scalar(
            select(SettingsLoginRequest)
            .where(SettingsLoginRequest.request_hash == request_hash)
            .with_for_update()
        )

    def get_settings_login_by_state_for_update(
        self,
        state_hash: str,
    ) -> SettingsLoginRequest | None:
        """Lock the matching OIDC attempt while its callback is completed."""

        return self._session.scalar(
            select(SettingsLoginRequest)
            .where(SettingsLoginRequest.oidc_state_hash == state_hash)
            .with_for_update()
        )

    def get_settings_session(self, session_hash: str) -> SettingsBrowserSession | None:
        return self._session.scalar(
            select(SettingsBrowserSession).where(
                SettingsBrowserSession.session_hash == session_hash
            )
        )

    def claim_telegram_update(self, update_id: int) -> bool:
        """Claim an update ID atomically, returning false when Telegram already delivered it."""

        try:
            with self._session.begin_nested():
                self._session.add(ProcessedTelegramUpdate(update_id=update_id))
                self._session.flush()
        except IntegrityError:
            return False
        return True

    def resolve_external_identity(
        self,
        *,
        provider: str,
        provider_user_id: str,
        provider_chat_id: str,
        provider_username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity:
        """Return one stable Atlas identity, creating its user on first contact."""

        identity = self.get_external_identity_by_provider(
            provider=provider,
            provider_user_id=provider_user_id,
        )
        if identity is not None:
            identity.provider_chat_id = provider_chat_id
            if provider_username is not None:
                identity.provider_username = provider_username
            user = self.get_user(identity.user_id)
            if user is not None:
                user.display_name = display_name
            return identity

        try:
            with self._session.begin_nested():
                user = User(display_name=display_name)
                self._session.add(user)
                self._session.flush()
                identity = ExternalIdentity(
                    user_id=user.id,
                    provider=provider,
                    provider_user_id=provider_user_id,
                    provider_chat_id=provider_chat_id,
                    provider_username=provider_username,
                )
                self._session.add(identity)
                self._session.flush()
            return identity
        except IntegrityError:
            # A concurrent first message may have created the same provider identity.
            identity = self.get_external_identity_by_provider(
                provider=provider,
                provider_user_id=provider_user_id,
            )
            if identity is None:
                raise
            identity.provider_chat_id = provider_chat_id
            if provider_username is not None:
                identity.provider_username = provider_username
            return identity

    def get_external_identity_by_provider(
        self,
        *,
        provider: str,
        provider_user_id: str,
    ) -> ExternalIdentity | None:
        return self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == provider,
                ExternalIdentity.provider_user_id == provider_user_id,
            )
        )

    def get_user_external_identity(
        self,
        *,
        user_id: UUID,
        provider: str,
    ) -> ExternalIdentity | None:
        return self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.user_id == user_id,
                ExternalIdentity.provider == provider,
            )
        )

    def get_external_identity(
        self,
        *,
        user_id: UUID,
        identity_id: UUID,
    ) -> ExternalIdentity | None:
        return self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.id == identity_id,
                ExternalIdentity.user_id == user_id,
            )
        )

    def get_integration(self, *, user_id: UUID, integration_id: UUID) -> Integration | None:
        return self._session.scalar(
            select(Integration).where(
                Integration.id == integration_id,
                Integration.user_id == user_id,
            )
        )

    def get_workflow(self, *, user_id: UUID, workflow_id: UUID) -> Workflow | None:
        return self._session.scalar(
            select(Workflow).where(
                Workflow.id == workflow_id,
                Workflow.user_id == user_id,
            )
        )
