from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from atlas.api.app import create_app
from atlas.config.config import Settings
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope
from atlas.telegram import InMemoryTelegram, TelegramButton, TelegramMessagingService
from atlas.telegram.browser_auth import VerifiedTelegramIdentity
from atlas.telegram.webhook import TELEGRAM_SECRET_HEADER


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class StubTelegramBrowserAuth:
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        return f"https://oauth.telegram.test/auth?state={state}"

    async def authenticate(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        expected_nonce_hash: str,
    ) -> VerifiedTelegramIdentity:
        raise NotImplementedError


@pytest.mark.anyio
async def test_webhook_verifies_secret_preserves_identity_and_deduplicates(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    telegram = InMemoryTelegram()
    settings = Settings(environment="test", telegram_webhook_secret="test-webhook-secret")
    app = create_app(
        settings,
        postgres_engine,
        telegram,
        telegram_browser_auth=StubTelegramBrowserAuth(),
    )
    transport = httpx.ASGITransport(app=app)
    provider_user_id = uuid4().int % 9_000_000_000 + 1
    chat_id = provider_user_id
    first_update_id = uuid4().int % 9_000_000_000 + 1
    second_update_id = first_update_id + 1
    callback_update_id = second_update_id + 1
    settings_update_id = callback_update_id + 1

    def message_update(update_id: int, text: str) -> dict[str, object]:
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "from": {
                    "id": provider_user_id,
                    "is_bot": False,
                    "first_name": "Atlas",
                    "last_name": "Tester",
                    "username": "atlas_tester",
                },
                "chat": {"id": chat_id, "type": "private"},
                "text": text,
            },
        }

    headers = {TELEGRAM_SECRET_HEADER: "test-webhook-secret"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        unauthorized = await client.post(
            "/webhooks/telegram",
            json=message_update(first_update_id, "Hello"),
        )
        first = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=message_update(first_update_id, "Hello"),
        )
        second_payload = message_update(second_update_id, "Still me")
        second = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=second_payload,
        )
        duplicate = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=second_payload,
        )
        settings_response = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=message_update(settings_update_id, "/settings"),
        )
        callback_payload = {
            "update_id": callback_update_id,
            "callback_query": {
                "id": "callback-1",
                "from": {
                    "id": provider_user_id,
                    "is_bot": False,
                    "first_name": "Atlas",
                    "last_name": "Tester",
                },
                "message": {
                    "message_id": 42,
                    "chat": {"id": chat_id, "type": "private"},
                },
                "data": "approve:1",
            },
        }
        callback = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=callback_payload,
        )
        duplicate_callback = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=callback_payload,
        )

    assert unauthorized.status_code == 401
    assert first.json() == {"status": "processed"}
    assert second.json() == {"status": "processed"}
    assert duplicate.json() == {"status": "duplicate"}
    assert settings_response.json() == {"status": "processed"}
    assert callback.json() == {"status": "processed"}
    assert duplicate_callback.json() == {"status": "duplicate"}
    assert [message.chat_id for message in telegram.sent_messages] == [chat_id, chat_id, chat_id]
    settings_button = telegram.sent_messages[-1].buttons[0]
    assert settings_button.url is not None
    assert settings_button.url.startswith("http://localhost:8080/settings#login=")
    assert len(telegram.callback_answers) == 1

    with session_scope(postgres_session_factory) as session:
        identity = AtlasRepository(session).get_external_identity_by_provider(
            provider="telegram",
            provider_user_id=str(provider_user_id),
        )
        assert identity is not None
        assert identity.provider_chat_id == str(chat_id)
        assert identity.provider_username == "atlas_tester"
        user_id = identity.user_id

    proactive = await TelegramMessagingService(
        postgres_session_factory,
        telegram,
    ).send_to_user(
        user_id=user_id,
        text="Your reminder is due.",
        buttons=(TelegramButton(text="Done", callback_data="task:done"),),
    )
    assert proactive.chat_id == chat_id
    assert proactive.buttons[0].callback_data == "task:done"

    with session_scope(postgres_session_factory) as session:
        identity = AtlasRepository(session).get_external_identity_by_provider(
            provider="telegram",
            provider_user_id=str(provider_user_id),
        )
        assert identity is not None
        assert identity.user_id == user_id
