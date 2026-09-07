import pytest
from pydantic import ValidationError

from atlas.config.config import Settings


def test_production_rejects_local_database() -> None:
    with pytest.raises(ValidationError, match="remote database host"):
        Settings(environment="production")


def test_blank_optional_secrets_are_not_stored() -> None:
    settings = Settings(telegram_bot_token="", google_client_secret="  ")

    assert settings.telegram_bot_token is None
    assert settings.google_client_secret is None


def test_telegram_webhook_secret_rejects_unsupported_characters() -> None:
    with pytest.raises(ValidationError, match="unsupported characters"):
        Settings(telegram_webhook_secret="not allowed")
