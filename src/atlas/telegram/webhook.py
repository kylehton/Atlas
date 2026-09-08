import secrets
from datetime import UTC, datetime
from typing import Literal, TypeGuard

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from atlas.db.models import ACCESS_REFERENCE_CODE_LENGTH, TelegramAccessRequestStatus
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.onboarding import SettingsLoginUnavailable, SettingsService
from atlas.shared.field_types import LONG_TEXT_MAX_LENGTH
from atlas.telegram.contracts import (
    TELEGRAM_PROVIDER,
    IncomingTelegramCallback,
    IncomingTelegramMessage,
    TelegramButton,
    TelegramCallbackData,
    TelegramCapability,
)

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
_CHECK_AC_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CHECK_CALLBACK_PREFIX = "access"

type AccessDecision = Literal["approve", "deny"]


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
        settings_service: SettingsService,
        *,
        admin_telegram_user_id: int | None,
    ) -> None:
        self._session_factory = session_factory
        self._telegram = telegram
        self._settings_service = settings_service
        self._admin_telegram_user_id = admin_telegram_user_id

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
        if len(message.text) > LONG_TEXT_MAX_LENGTH:
            await self._telegram.send_message(
                chat_id=message.chat.id,
                text="That message is too long for Atlas to process.",
            )
            return

        identity = repository.get_external_identity_by_provider(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(message.sender.id),
        )
        if identity is None and message.sender.id != self._admin_telegram_user_id:
            await self._process_access_request(repository, message)
            return

        identity = repository.resolve_external_identity(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(message.sender.id),
            provider_chat_id=str(message.chat.id),
            provider_username=message.sender.username,
            display_name=message.sender.display_name,
        )
        incoming = IncomingTelegramMessage(
            update_id=update_id,
            user_id=identity.user_id,
            provider_user_id=message.sender.id,
            chat_id=message.chat.id,
            text=message.text,
        )
        if incoming.text.strip().lower() in {"/notifications", "notifications"}:
            try:
                login = self._settings_service.issue_login_request_with_repository(
                    repository,
                    incoming.user_id,
                )
            except SettingsLoginUnavailable:
                await self._telegram.send_message(
                    chat_id=incoming.chat_id,
                    text="Atlas notification settings login is not configured yet.",
                )
                return
            await self._telegram.send_message(
                chat_id=incoming.chat_id,
                text="Open your private Atlas notification settings link. It expires shortly.",
                buttons=(TelegramButton(text="Open notifications", url=login.url),),
            )
            return
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
        access_decision = _parse_access_decision(callback.data)
        if access_decision is not None:
            decision, reference_code = access_decision
            await self._process_access_decision(
                repository,
                callback,
                decision=decision,
                reference_code=reference_code,
            )
            return

        existing_identity = repository.get_external_identity_by_provider(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(callback.sender.id),
        )
        if existing_identity is None:
            await self._telegram.answer_callback_query(
                query_id=callback.id,
                text="Atlas access is required.",
            )
            return

        identity = repository.resolve_external_identity(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(callback.sender.id),
            provider_chat_id=str(callback.message.chat.id),
            provider_username=callback.sender.username,
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

    async def _process_access_request(
        self,
        repository: AtlasRepository,
        message: TelegramMessagePayload,
    ) -> None:
        """Create one pending request and notify both the requester and configured admin."""

        assert message.sender is not None
        if self._admin_telegram_user_id is None:
            await self._telegram.send_message(
                chat_id=message.chat.id,
                text="Atlas access requests are not configured yet.",
            )
            return

        access_request, created = repository.get_or_create_telegram_access_request(
            telegram_user_id=message.sender.id,
            telegram_chat_id=message.chat.id,
            telegram_username=message.sender.username,
            display_name=message.sender.display_name,
            reference_code=_new_access_reference_code(),
        )
        if access_request.status is TelegramAccessRequestStatus.DENIED:
            await self._telegram.send_message(
                chat_id=message.chat.id,
                text=f"Atlas access request {access_request.reference_code} was denied.",
            )
            return
        if access_request.status is TelegramAccessRequestStatus.APPROVED:
            await self._telegram.send_message(
                chat_id=message.chat.id,
                text="Atlas access is approved. Send /notifications to open your preferences.",
            )
            return
        if not created:
            await self._telegram.send_message(
                chat_id=message.chat.id,
                text=(
                    f"Atlas access request {access_request.reference_code} is awaiting approval."
                ),
            )
            return

        username = (
            f"@{access_request.telegram_username}"
            if access_request.telegram_username is not None
            else "No Telegram username"
        )
        await self._telegram.send_message(
            chat_id=self._admin_telegram_user_id,
            text=(
                f"Atlas access request {access_request.reference_code}\n"
                f"{access_request.display_name} ({username})\n"
                f"Telegram user: {access_request.telegram_user_id}"
            ),
            buttons=(
                TelegramButton(
                    text="Approve",
                    callback_data=f"{_CHECK_CALLBACK_PREFIX}:approve:"
                    f"{access_request.reference_code}",
                ),
                TelegramButton(
                    text="Deny",
                    callback_data=f"{_CHECK_CALLBACK_PREFIX}:deny:{access_request.reference_code}",
                ),
            ),
        )
        await self._telegram.send_message(
            chat_id=message.chat.id,
            text=(
                f"Your Atlas access request is {access_request.reference_code}. "
                "You will receive a message after it is reviewed."
            ),
        )

    async def _process_access_decision(
        self,
        repository: AtlasRepository,
        callback: TelegramCallbackQueryPayload,
        *,
        decision: AccessDecision,
        reference_code: str,
    ) -> None:
        """Apply one admin-authorized decision without repeating its effects."""

        if callback.sender.id != self._admin_telegram_user_id:
            await self._telegram.answer_callback_query(
                query_id=callback.id,
                text="Only the configured Atlas admin can review access requests.",
            )
            return

        access_request = repository.get_telegram_access_request_by_code_for_update(reference_code)
        if access_request is None:
            await self._telegram.answer_callback_query(
                query_id=callback.id,
                text="Access request not found.",
            )
            return
        if access_request.status is not TelegramAccessRequestStatus.PENDING:
            await self._telegram.answer_callback_query(
                query_id=callback.id,
                text=f"Access request already {access_request.status.value}.",
            )
            return

        access_request.decided_at = datetime.now(UTC)
        access_request.decided_by_telegram_user_id = callback.sender.id
        if decision == "deny":
            access_request.status = TelegramAccessRequestStatus.DENIED
            await self._telegram.send_message(
                chat_id=access_request.telegram_chat_id,
                text=f"Atlas access request {reference_code} was denied.",
            )
            await self._telegram.answer_callback_query(
                query_id=callback.id,
                text=f"Denied {reference_code}.",
            )
            return

        identity = repository.resolve_external_identity(
            provider=TELEGRAM_PROVIDER,
            provider_user_id=str(access_request.telegram_user_id),
            provider_chat_id=str(access_request.telegram_chat_id),
            provider_username=access_request.telegram_username,
            display_name=access_request.display_name,
        )
        access_request.status = TelegramAccessRequestStatus.APPROVED
        access_request.user_id = identity.user_id
        buttons: tuple[TelegramButton, ...] = ()
        try:
            login = self._settings_service.issue_login_request_with_repository(
                repository,
                identity.user_id,
            )
        except SettingsLoginUnavailable:
            pass
        else:
            buttons = (TelegramButton(text="Open notifications", url=login.url),)
        await self._telegram.send_message(
            chat_id=access_request.telegram_chat_id,
            text=f"Atlas access request {reference_code} was approved.",
            buttons=buttons,
        )
        await self._telegram.answer_callback_query(
            query_id=callback.id,
            text=f"Approved {reference_code}.",
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


def _new_access_reference_code() -> str:
    return "".join(
        secrets.choice(_CHECK_AC_ALPHABET) for _ in range(ACCESS_REFERENCE_CODE_LENGTH)
    )


def _parse_access_decision(value: str) -> tuple[AccessDecision, str] | None:
    parts = value.split(":")
    if (
        len(parts) != 3
        or parts[0] != _CHECK_CALLBACK_PREFIX
        or parts[1] not in {"approve", "deny"}
        or len(parts[2]) != ACCESS_REFERENCE_CODE_LENGTH
        or any(character not in _CHECK_AC_ALPHABET for character in parts[2])
    ):
        return None
    decision: AccessDecision = "approve" if parts[1] == "approve" else "deny"
    return decision, parts[2]


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
