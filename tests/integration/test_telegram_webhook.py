from uuid import uuid4

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from atlas.api.app import create_app
from atlas.config.config import Settings
from atlas.db.models import TelegramAccessRequest, TelegramAccessRequestStatus
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


def _message_update(
    update_id: int,
    *,
    provider_user_id: int,
    text: str,
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {
                "id": provider_user_id,
                "is_bot": False,
                "first_name": "Atlas",
                "last_name": "Tester",
                "username": f"atlas_tester_{provider_user_id}",
            },
            "chat": {"id": provider_user_id, "type": "private"},
            "text": text,
        },
    }


def _callback_update(
    update_id: int,
    *,
    provider_user_id: int,
    callback_data: str,
) -> dict[str, object]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"callback-{update_id}",
            "from": {
                "id": provider_user_id,
                "is_bot": False,
                "first_name": "Atlas",
                "last_name": "Admin",
            },
            "message": {
                "message_id": update_id,
                "chat": {"id": provider_user_id, "type": "private"},
            },
            "data": callback_data,
        },
    }


@pytest.mark.anyio
async def test_webhook_verifies_secret_preserves_identity_and_deduplicates(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    telegram = InMemoryTelegram()
    provider_user_id = uuid4().int % 9_000_000_000 + 1
    settings = Settings(
        environment="test",
        telegram_webhook_secret="test-webhook-secret",
        telegram_admin_user_id=provider_user_id,
    )
    app = create_app(
        settings,
        postgres_engine,
        telegram,
        telegram_browser_auth=StubTelegramBrowserAuth(),
    )
    transport = httpx.ASGITransport(app=app)
    chat_id = provider_user_id
    first_update_id = uuid4().int % 9_000_000_000 + 1
    second_update_id = first_update_id + 1
    callback_update_id = second_update_id + 1
    settings_update_id = callback_update_id + 1

    def message_update(update_id: int, text: str) -> dict[str, object]:
        return _message_update(
            update_id,
            provider_user_id=provider_user_id,
            text=text,
        )

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
            json=message_update(settings_update_id, "/notifications"),
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
    assert settings_button.url.startswith("http://localhost:8080/notifications#login=")
    assert len(telegram.callback_answers) == 1

    with session_scope(postgres_session_factory) as session:
        identity = AtlasRepository(session).get_external_identity_by_provider(
            provider="telegram",
            provider_user_id=str(provider_user_id),
        )
        assert identity is not None
        assert identity.provider_chat_id == str(chat_id)
        assert identity.provider_username == f"atlas_tester_{provider_user_id}"
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


@pytest.mark.anyio
async def test_unknown_user_gets_one_stable_pending_access_request(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    telegram = InMemoryTelegram()
    admin_user_id = uuid4().int % 9_000_000_000 + 1
    requester_user_id = admin_user_id + 1
    app = create_app(
        Settings(
            environment="test",
            telegram_webhook_secret="test-webhook-secret",
            telegram_admin_user_id=admin_user_id,
        ),
        postgres_engine,
        telegram,
        telegram_browser_auth=StubTelegramBrowserAuth(),
    )
    headers = {TELEGRAM_SECRET_HEADER: "test-webhook-secret"}
    first_update_id = uuid4().int % 9_000_000_000 + 1
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_message_update(
                first_update_id,
                provider_user_id=requester_user_id,
                text="Hello",
            ),
        )
        repeated = await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_message_update(
                first_update_id + 1,
                provider_user_id=requester_user_id,
                text="Any update?",
            ),
        )

    assert first.json() == repeated.json() == {"status": "processed"}
    assert [message.chat_id for message in telegram.sent_messages] == [
        admin_user_id,
        requester_user_id,
        requester_user_id,
    ]
    assert len(telegram.sent_messages[0].buttons) == 2
    with session_scope(postgres_session_factory) as session:
        request = session.scalar(
            select(TelegramAccessRequest).where(
                TelegramAccessRequest.telegram_user_id == requester_user_id
            )
        )
        assert request is not None
        assert request.status is TelegramAccessRequestStatus.PENDING
        assert request.reference_code in telegram.sent_messages[1].text
        assert request.reference_code in telegram.sent_messages[2].text
        assert (
            AtlasRepository(session).get_external_identity_by_provider(
                provider="telegram",
                provider_user_id=str(requester_user_id),
            )
            is None
        )


@pytest.mark.anyio
async def test_only_admin_can_approve_access_and_repeated_decision_is_idempotent(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    telegram = InMemoryTelegram()
    admin_user_id = uuid4().int % 9_000_000_000 + 1
    requester_user_id = admin_user_id + 1
    other_user_id = admin_user_id + 2
    app = create_app(
        Settings(
            environment="test",
            telegram_webhook_secret="test-webhook-secret",
            telegram_admin_user_id=admin_user_id,
        ),
        postgres_engine,
        telegram,
        telegram_browser_auth=StubTelegramBrowserAuth(),
    )
    headers = {TELEGRAM_SECRET_HEADER: "test-webhook-secret"}
    first_update_id = uuid4().int % 9_000_000_000 + 1
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_message_update(
                first_update_id,
                provider_user_id=requester_user_id,
                text="Hello",
            ),
        )
        approve_data = telegram.sent_messages[0].buttons[0].callback_data
        assert approve_data is not None
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_callback_update(
                first_update_id + 1,
                provider_user_id=other_user_id,
                callback_data=approve_data,
            ),
        )
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_callback_update(
                first_update_id + 2,
                provider_user_id=admin_user_id,
                callback_data=approve_data,
            ),
        )
        sent_after_approval = len(telegram.sent_messages)
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_callback_update(
                first_update_id + 3,
                provider_user_id=admin_user_id,
                callback_data=approve_data,
            ),
        )

    assert "Only the configured Atlas admin" in (telegram.callback_answers[0].text or "")
    assert telegram.callback_answers[1].text is not None
    assert telegram.callback_answers[1].text.startswith("Approved ")
    assert telegram.callback_answers[2].text is not None
    assert "already approved" in telegram.callback_answers[2].text
    assert len(telegram.sent_messages) == sent_after_approval == 3
    approval_message = telegram.sent_messages[-1]
    assert approval_message.chat_id == requester_user_id
    assert len(approval_message.buttons) == 1
    assert approval_message.buttons[0].url is not None
    assert approval_message.buttons[0].url.startswith("http://localhost:8080/notifications#login=")
    with session_scope(postgres_session_factory) as session:
        access_request = session.scalar(
            select(TelegramAccessRequest).where(
                TelegramAccessRequest.telegram_user_id == requester_user_id
            )
        )
        identity = AtlasRepository(session).get_external_identity_by_provider(
            provider="telegram",
            provider_user_id=str(requester_user_id),
        )
        assert access_request is not None
        assert access_request.status is TelegramAccessRequestStatus.APPROVED
        assert access_request.decided_by_telegram_user_id == admin_user_id
        assert identity is not None
        assert access_request.user_id == identity.user_id


@pytest.mark.anyio
async def test_denied_access_request_remains_blocked(
    postgres_engine: Engine,
    postgres_session_factory: sessionmaker[Session],
) -> None:
    telegram = InMemoryTelegram()
    admin_user_id = uuid4().int % 9_000_000_000 + 1
    requester_user_id = admin_user_id + 1
    app = create_app(
        Settings(
            environment="test",
            telegram_webhook_secret="test-webhook-secret",
            telegram_admin_user_id=admin_user_id,
        ),
        postgres_engine,
        telegram,
        telegram_browser_auth=StubTelegramBrowserAuth(),
    )
    headers = {TELEGRAM_SECRET_HEADER: "test-webhook-secret"}
    first_update_id = uuid4().int % 9_000_000_000 + 1
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_message_update(
                first_update_id,
                provider_user_id=requester_user_id,
                text="Hello",
            ),
        )
        deny_data = telegram.sent_messages[0].buttons[1].callback_data
        assert deny_data is not None
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_callback_update(
                first_update_id + 1,
                provider_user_id=admin_user_id,
                callback_data=deny_data,
            ),
        )
        await client.post(
            "/webhooks/telegram",
            headers=headers,
            json=_message_update(
                first_update_id + 2,
                provider_user_id=requester_user_id,
                text="Try again",
            ),
        )

    assert "was denied" in telegram.sent_messages[-1].text
    with session_scope(postgres_session_factory) as session:
        access_request = session.scalar(
            select(TelegramAccessRequest).where(
                TelegramAccessRequest.telegram_user_id == requester_user_id
            )
        )
        assert access_request is not None
        assert access_request.status is TelegramAccessRequestStatus.DENIED
        assert (
            AtlasRepository(session).get_external_identity_by_provider(
                provider="telegram",
                provider_user_id=str(requester_user_id),
            )
            is None
        )
