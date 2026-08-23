from atlas.shared.field_types import LongText
from atlas.telegram.contracts import SentTelegramMessage, TelegramButton


class InMemoryTelegram:
    """Record outgoing Telegram messages instead of making network requests."""

    def __init__(self) -> None:
        self.sent_messages: list[SentTelegramMessage] = []

    async def send_message(
        self,
        *,
        chat_id: int,
        text: LongText,
        buttons: tuple[TelegramButton, ...] = (),
    ) -> SentTelegramMessage:
        """Create a deterministic sent-message record and return it."""

        message = SentTelegramMessage(
            id=len(self.sent_messages) + 1,
            chat_id=chat_id,
            text=text,
            buttons=buttons,
        )
        self.sent_messages.append(message)
        return message
