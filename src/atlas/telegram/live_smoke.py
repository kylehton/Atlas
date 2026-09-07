import argparse
import asyncio
from uuid import UUID

from atlas.config.config import Settings
from atlas.db.session import create_database_engine, create_session_factory
from atlas.telegram.client import TelegramBotClient
from atlas.telegram.contracts import TelegramButton
from atlas.telegram.messaging import TelegramMessagingService


async def send_test_button(user_id: UUID) -> None:
    """Send the interactive portion of the live smoke test through Atlas's real adapter."""

    settings = Settings()
    if settings.telegram_bot_token is None:
        raise RuntimeError("ATLAS_TELEGRAM_BOT_TOKEN is required")

    engine = create_database_engine(settings)
    telegram = TelegramBotClient(settings.telegram_bot_token)
    try:
        message = await TelegramMessagingService(
            create_session_factory(engine),
            telegram,
        ).send_to_user(
            user_id=user_id,
            text="Atlas live smoke test: press the button below.",
            buttons=(TelegramButton(text="Acknowledge", callback_data="atlas.live-smoke.ack"),),
        )
        print(f"Sent Telegram test message {message.id}.")
    finally:
        await telegram.aclose()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Atlas's live Telegram smoke-test button")
    parser.add_argument("user_id", type=UUID)
    arguments = parser.parse_args()
    asyncio.run(send_test_button(arguments.user_id))


if __name__ == "__main__":
    main()
