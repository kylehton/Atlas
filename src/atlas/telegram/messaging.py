from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.shared.field_types import LongText
from atlas.telegram.contracts import (
    TELEGRAM_PROVIDER,
    SentTelegramMessage,
    TelegramButton,
    TelegramCapability,
)


class TelegramIdentityNotFound(LookupError):
    """Indicate that an Atlas user has no Telegram destination."""


class TelegramMessagingService:
    """Send proactive Telegram messages using an Atlas user ID instead of a chat ID."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        telegram: TelegramCapability,
    ) -> None:
        self._session_factory = session_factory
        self._telegram = telegram

    async def send_to_user(
        self,
        *,
        user_id: UUID,
        text: LongText,
        buttons: tuple[TelegramButton, ...] = (),
    ) -> SentTelegramMessage:
        with session_scope(self._session_factory) as session:
            identity = AtlasRepository(session).get_user_external_identity(
                user_id=user_id,
                provider=TELEGRAM_PROVIDER,
            )
            if identity is None:
                raise TelegramIdentityNotFound(f"user {user_id} has no Telegram identity")
            chat_id = int(identity.provider_chat_id)

        return await self._telegram.send_message(
            chat_id=chat_id,
            text=text,
            buttons=buttons,
        )
