import json

import httpx
import pytest
from pydantic import SecretStr

from atlas.telegram import TelegramBotClient, TelegramButton


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_bot_client_sends_buttons_and_answers_callbacks() -> None:
    requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/sendMessage"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"message_id": 42, "chat": {"id": 1001}}},
            )
        return httpx.Response(200, json={"ok": True, "result": True})

    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as http_client:
        telegram = TelegramBotClient(SecretStr("test-token"), client=http_client)
        sent = await telegram.send_message(
            chat_id=1001,
            text="Approve this action?",
            buttons=(TelegramButton(text="Approve", callback_data="approve:1"),),
        )
        await telegram.answer_callback_query(query_id="callback-1", text="Approved")

    sent_payload = json.loads(requests[0].content)
    callback_payload = json.loads(requests[1].content)
    assert sent.id == 42
    assert sent_payload == {
        "chat_id": 1001,
        "text": "Approve this action?",
        "reply_markup": {"inline_keyboard": [[{"text": "Approve", "callback_data": "approve:1"}]]},
    }
    assert callback_payload == {"callback_query_id": "callback-1", "text": "Approved"}
