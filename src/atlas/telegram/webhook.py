import secrets
from typing import Literal, TypeGuard

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.shared.field_types import LONG_TEXT_MAX_LENGTH
from atlas.telegram.contracts import (
    TELEGRAM_PROVIDER,
    IncomingTelegramCallback,
    IncomingTelegramMessage,
    TelegramCallbackData,
    TelegramCapability,
)

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class TelegramUserPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    is_bot: bool
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)
    username: str | None = Field(default=None, max_length=32)

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)


class TelegramChatPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    type: str


class TelegramMessagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: int
    sender: TelegramUserPayload | None = Field(default=None, alias="from")
    chat: TelegramChatPayload
    text: str | None = Field(default=None, max_length=4_096)


class TelegramCallbackQueryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str = Field(min_length=1, max_length=256)
    sender: TelegramUserPayload = Field(alias="from")
    message: TelegramMessagePayload | None = None
    data: TelegramCallbackData | None = None


class TelegramUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    update_id: int = Field(ge=0)
    message: TelegramMessagePayload | None = None
    callback_query: TelegramCallbackQueryPayload | None = None


class TelegramWebhookService:
    """Normalize supported updates, map identities, and perform the initial Telegram response."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        telegram: TelegramCapability,
    ) -> None:
        self._session_factory = session_factory
        self._telegram = telegram

    async def process(
        self,
        update: TelegramUpdatePayload,
    ) -> Literal["processed", "duplicate", "ignored"]:
        with session_scope(self._session_factory) as session:
            repository = AtlasRepository(session)
            if not repository.claim_telegram_update(update.update_id):
                return "duplicate"

            if self._is_supported_message(update.message):
                await self._process_message(repository, update.update_id, update.message)
                return "processed"

            callback = update.callback_query
            if self._is_supported_callback(callback):
                await self._process_callback(repository, update.update_id, callback)
                return "processed"

            return "ignored"

    async def _process_message(
        self,
        repository: AtlasRepository,
        update_id: int,
        message: TelegramMessagePayload,
    ) -> None:
        assert message.sender is not None
        assert message.text is not None
        identity = repository.resolve_external_identity(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(message.sender.id),
            provider_chat_id=str(message.chat.id),
            display_name=message.sender.display_name,
        )
        if len(message.text) > LONG_TEXT_MAX_LENGTH:
            await self._telegram.send_message(
                chat_id=message.chat.id,
                text="That message is too long for Atlas to process.",
            )
            return

        incoming = IncomingTelegramMessage(
            update_id=update_id,
            user_id=identity.user_id,
            provider_user_id=message.sender.id,
            chat_id=message.chat.id,
            text=message.text,
        )
        await self._telegram.send_message(
            chat_id=incoming.chat_id,
            text="Atlas is connected and received your message.",
        )

    async def _process_callback(
        self,
        repository: AtlasRepository,
        update_id: int,
        callback: TelegramCallbackQueryPayload,
    ) -> None:
        assert callback.message is not None
        assert callback.data is not None
        identity = repository.resolve_external_identity(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(callback.sender.id),
            provider_chat_id=str(callback.message.chat.id),
            display_name=callback.sender.display_name,
        )
        incoming = IncomingTelegramCallback(
            update_id=update_id,
            user_id=identity.user_id,
            provider_user_id=callback.sender.id,
            chat_id=callback.message.chat.id,
            query_id=callback.id,
            data=callback.data,
        )
        await self._telegram.answer_callback_query(
            query_id=incoming.query_id,
            text="Received.",
        )

    @staticmethod
    def _is_supported_message(
        message: TelegramMessagePayload | None,
    ) -> TypeGuard[TelegramMessagePayload]:
        return bool(
            message is not None
            and message.sender is not None
            and not message.sender.is_bot
            and message.chat.type == "private"
            and message.text
        )

    @staticmethod
    def _is_supported_callback(
        callback: TelegramCallbackQueryPayload | None,
    ) -> TypeGuard[TelegramCallbackQueryPayload]:
        return bool(
            callback is not None
            and not callback.sender.is_bot
            and callback.message is not None
            and callback.message.chat.type == "private"
            and callback.data
        )


router = APIRouter()


@router.post("/webhooks/telegram")
async def receive_telegram_webhook(request: Request) -> dict[str, str]:
    """Authenticate Telegram before parsing and processing its webhook payload."""

    expected_secret = request.app.state.settings.telegram_webhook_secret
    service: TelegramWebhookService | None = request.app.state.telegram_webhook_service
    if expected_secret is None or service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram is not configured",
        )

    provided_secret = request.headers.get(TELEGRAM_SECRET_HEADER)
    if provided_secret is None or not secrets.compare_digest(
        provided_secret,
        expected_secret.get_secret_value(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret"
        )

    try:
        payload = TelegramUpdatePayload.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram update",
        ) from None

    result = await service.process(payload)
    return {"status": result}
