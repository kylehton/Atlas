from typing import Protocol

from pydantic import PositiveInt
from pydantic.dataclasses import dataclass

from atlas.shared.field_types import LongText, ShortText


@dataclass(frozen=True, slots=True)
class TelegramButton:
    text: ShortText
    callback_data: ShortText


@dataclass(frozen=True, slots=True)
class SentTelegramMessage:
    id: PositiveInt
    chat_id: int
    text: LongText
    buttons: tuple[TelegramButton, ...] = ()


class TelegramCapability(Protocol):
    async def send_message(
        self,
        *,
        chat_id: int,
        text: LongText,
        buttons: tuple[TelegramButton, ...] = (),
    ) -> SentTelegramMessage:
        """Send a Telegram message."""
