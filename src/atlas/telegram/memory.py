from atlas.shared.field_types import ExternalId, LongText, ShortText
from atlas.telegram.contracts import (
    SentTelegramMessage,
    TelegramButton,
    TelegramCallbackAnswer,
)


class InMemoryTelegram:
    """Record outgoing Telegram messages instead of making network requests."""

    def __init__(self) -> None:
        self.sent_messages: list[SentTelegramMessage] = []
        self.callback_answers: list[TelegramCallbackAnswer] = []

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

    async def answer_callback_query(
        self,
        *,
        query_id: ExternalId,
        text: ShortText | None = None,
    ) -> None:
        """Record a callback acknowledgement instead of contacting Telegram."""

        self.callback_answers.append(TelegramCallbackAnswer(query_id=query_id, text=text))
