from typing import Annotated, Protocol
from uuid import UUID

from pydantic import AfterValidator, PositiveInt, StringConstraints
from pydantic.dataclasses import dataclass

from atlas.shared.field_types import ExternalId, LongText, ShortText

TELEGRAM_PROVIDER = "telegram"


def _validate_callback_data_size(value: str) -> str:
    """Enforce Telegram's 64-byte callback-data limit, including non-ASCII text."""

    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback data must not exceed 64 bytes")
    return value


type TelegramCallbackData = Annotated[
    str,
    StringConstraints(min_length=1),
    AfterValidator(_validate_callback_data_size),
]


@dataclass(frozen=True, slots=True)
class TelegramButton:
    text: ShortText
    callback_data: TelegramCallbackData | None = None
    url: LongText | None = None

    def __post_init__(self) -> None:
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("Telegram buttons require exactly one callback_data or url value")


@dataclass(frozen=True, slots=True)
class SentTelegramMessage:
    id: PositiveInt
    chat_id: int
    text: LongText
    buttons: tuple[TelegramButton, ...] = ()


@dataclass(frozen=True, slots=True)
class IncomingTelegramMessage:
    update_id: int
    user_id: UUID
    provider_user_id: int
    chat_id: int
    text: LongText


@dataclass(frozen=True, slots=True)
class IncomingTelegramCallback:
    update_id: int
    user_id: UUID
    provider_user_id: int
    chat_id: int
    query_id: ExternalId
    data: TelegramCallbackData


@dataclass(frozen=True, slots=True)
class TelegramCallbackAnswer:
    query_id: ExternalId
    text: ShortText | None = None


class TelegramCapability(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: LongText,
        buttons: tuple[TelegramButton, ...] = (),
    ) -> SentTelegramMessage:
        """Send a Telegram message."""

    async def answer_callback_query(
        self,
        *,
        query_id: ExternalId,
        text: ShortText | None = None,
    ) -> None:
        """Acknowledge an inline-button callback so Telegram clears its progress indicator."""
