from typing import Any

import httpx
from pydantic import SecretStr

from atlas.shared.field_types import ExternalId, LongText, ShortText
from atlas.telegram.contracts import SentTelegramMessage, TelegramButton


class TelegramAPIError(RuntimeError):
    """Represent a sanitized Telegram Bot API transport or response failure."""


class TelegramBotClient:
    """Send messages and callback acknowledgements through the Telegram Bot API."""

    def __init__(
        self,
        token: SecretStr,
        *,
        client: httpx.AsyncClient | None = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._token = token.get_secret_value()
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._api_base_url = api_base_url.rstrip("/")

    async def send_message(
        self,
        *,
        chat_id: int,
        text: LongText,
        buttons: tuple[TelegramButton, ...] = (),
    ) -> SentTelegramMessage:
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": button.text, "callback_data": button.callback_data}]
                    for button in buttons
                ]
            }

        result = await self._post("sendMessage", payload)
        message_id = result.get("message_id")
        result_chat = result.get("chat")
        if not isinstance(message_id, int) or not isinstance(result_chat, dict):
            raise TelegramAPIError("Telegram returned an invalid message response")
        result_chat_id = result_chat.get("id")
        if not isinstance(result_chat_id, int):
            raise TelegramAPIError("Telegram returned an invalid message response")
        return SentTelegramMessage(
            id=message_id,
            chat_id=result_chat_id,
            text=text,
            buttons=buttons,
        )

    async def answer_callback_query(
        self,
        *,
        query_id: ExternalId,
        text: ShortText | None = None,
    ) -> None:
        payload: dict[str, object] = {"callback_query_id": query_id}
        if text is not None:
            payload["text"] = text
        await self._post("answerCallbackQuery", payload)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(self, method: str, payload: dict[str, object]) -> dict[str, Any]:
        """Call one Bot API method without leaking the bot token through raised errors."""

        url = f"{self._api_base_url}/bot{self._token}/{method}"
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            # HTTPX errors can contain the token-bearing request URL.
            raise TelegramAPIError("Telegram request failed") from None

        if not isinstance(body, dict) or body.get("ok") is not True:
            raise TelegramAPIError("Telegram rejected the request")
        result = body.get("result")
        if isinstance(result, dict):
            return result
        if result is True:
            return {}
        raise TelegramAPIError("Telegram returned an invalid response")
