from dataclasses import dataclass

import httpx

from atlas.config.config import Settings
from atlas.shared.field_types import ShortText


class TelegramUserIdLookupError(RuntimeError):
    """Report a safe setup error without exposing the bot token."""


@dataclass(frozen=True, slots=True)
class TelegramSender:
    user_id: int
    display_name: ShortText
    username: ShortText | None


def extract_latest_private_sender(
    updates: object,
    *,
    after_update_id: int | None = None,
) -> TelegramSender | None:
    """Return the newest human sender from a private Telegram message update."""

    if not isinstance(updates, list):
        return None
    for update in reversed(updates):
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or (
            after_update_id is not None and update_id <= after_update_id
        ):
            continue
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat")
        sender = message.get("from")
        if (
            not isinstance(chat, dict)
            or chat.get("type") != "private"
            or not isinstance(sender, dict)
            or sender.get("is_bot") is not False
        ):
            continue
        user_id = sender.get("id")
        first_name = sender.get("first_name")
        last_name = sender.get("last_name")
        username = sender.get("username")
        if not isinstance(user_id, int) or not isinstance(first_name, str):
            continue
        display_name = " ".join(
            value for value in (first_name, last_name) if isinstance(value, str) and value
        )
        return TelegramSender(
            user_id=user_id,
            display_name=display_name,
            username=username if isinstance(username, str) else None,
        )
    return None


def _telegram_request(
    client: httpx.Client,
    token: str,
    method: str,
    payload: dict[str, object],
) -> object:
    try:
        response = client.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise TelegramUserIdLookupError("Telegram request failed") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise TelegramUserIdLookupError("Telegram rejected the request")
    return body.get("result")


def _latest_update_id(updates: object) -> int | None:
    if not isinstance(updates, list):
        return None
    update_ids = [
        update["update_id"]
        for update in updates
        if isinstance(update, dict) and isinstance(update.get("update_id"), int)
    ]
    return max(update_ids, default=None)


def run() -> None:
    settings = Settings()
    if settings.telegram_bot_token is None:
        raise TelegramUserIdLookupError("Set ATLAS_TELEGRAM_BOT_TOKEN in .env first")
    token = settings.telegram_bot_token.get_secret_value()

    with httpx.Client(timeout=15.0) as client:
        bot = _telegram_request(client, token, "getMe", {})
        webhook = _telegram_request(client, token, "getWebhookInfo", {})
        if not isinstance(bot, dict) or not isinstance(webhook, dict):
            raise TelegramUserIdLookupError("Telegram returned invalid bot information")
        webhook_url = webhook.get("url")
        if isinstance(webhook_url, str) and webhook_url:
            raise TelegramUserIdLookupError(
                "The bot has an active webhook. Stop the live test before looking up your ID."
            )
        username = bot.get("username")
        if not isinstance(username, str) or not username:
            raise TelegramUserIdLookupError("Telegram bot has no username")

        existing_updates = _telegram_request(client, token, "getUpdates", {"timeout": 0})
        previous_update_id = _latest_update_id(existing_updates)
        print(f"Send a new private message to https://t.me/{username}.")
        input("Press Enter after the message is sent... ")
        payload: dict[str, object] = {"timeout": 10, "allowed_updates": ["message"]}
        if previous_update_id is not None:
            payload["offset"] = previous_update_id + 1
        updates = _telegram_request(client, token, "getUpdates", payload)

    sender = extract_latest_private_sender(updates, after_update_id=previous_update_id)
    if sender is None:
        raise TelegramUserIdLookupError(
            "No new private message was received; run the command again"
        )
    username_label = f" (@{sender.username})" if sender.username is not None else ""
    print(f"Telegram account: {sender.display_name}{username_label}")
    print(f"Your numeric Telegram user ID is: {sender.user_id}")
    print(f"Add ATLAS_TELEGRAM_ADMIN_USER_ID={sender.user_id} to .env")


def main() -> None:
    try:
        run()
    except TelegramUserIdLookupError as error:
        raise SystemExit(f"Telegram user ID lookup failed: {error}") from None


if __name__ == "__main__":
    main()
