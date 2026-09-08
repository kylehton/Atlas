from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine

from atlas.api.health import router as health_router
from atlas.api.middleware import add_request_context
from atlas.config.config import Settings, get_settings
from atlas.config.observability import configure_logging
from atlas.db.session import create_database_engine, create_session_factory
from atlas.onboarding import SettingsService
from atlas.onboarding.routes import router as settings_router
from atlas.telegram import TelegramBotClient, TelegramCapability, TelegramMessagingService
from atlas.telegram.browser_auth import TelegramBrowserAuth, TelegramOidcClient
from atlas.telegram.webhook import TelegramWebhookService
from atlas.telegram.webhook import router as telegram_router


def create_app(
    settings: Settings | None = None,
    database_engine: Engine | None = None,
    telegram: TelegramCapability | None = None,
    telegram_browser_auth: TelegramBrowserAuth | None = None,
) -> FastAPI:
    """Create the API with explicit dependencies when provided, or configured defaults."""

    resolved_settings = settings or get_settings()
    resolved_engine = database_engine or create_database_engine(resolved_settings)
    session_factory = create_session_factory(resolved_engine)
    resolved_browser_auth = telegram_browser_auth
    if (
        resolved_browser_auth is None
        and resolved_settings.telegram_login_client_id is not None
        and resolved_settings.telegram_login_client_secret is not None
    ):
        resolved_browser_auth = TelegramOidcClient(
            resolved_settings.telegram_login_client_id,
            resolved_settings.telegram_login_client_secret,
        )
    settings_service = SettingsService(
        session_factory,
        public_base_url=str(resolved_settings.public_base_url),
        telegram_auth=resolved_browser_auth,
        login_request_ttl=timedelta(minutes=resolved_settings.settings_login_request_ttl_minutes),
        session_ttl=timedelta(hours=resolved_settings.settings_session_ttl_hours),
    )
    resolved_telegram = telegram
    if resolved_telegram is None and resolved_settings.telegram_bot_token is not None:
        resolved_telegram = TelegramBotClient(resolved_settings.telegram_bot_token)
    telegram_service = (
        TelegramWebhookService(session_factory, resolved_telegram, settings_service)
        if resolved_telegram is not None
        else None
    )
    telegram_messaging_service = (
        TelegramMessagingService(session_factory, resolved_telegram)
        if resolved_telegram is not None
        else None
    )
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if isinstance(resolved_telegram, TelegramBotClient):
            await resolved_telegram.aclose()
        if isinstance(resolved_browser_auth, TelegramOidcClient):
            await resolved_browser_auth.aclose()
        resolved_engine.dispose()

    application = FastAPI(title="Atlas", version="0.1.0", lifespan=lifespan)
    application.state.settings = resolved_settings
    application.state.database_engine = resolved_engine
    application.state.telegram_webhook_service = telegram_service
    application.state.telegram_messaging_service = telegram_messaging_service
    application.state.settings_service = settings_service
    add_request_context(application)
    application.include_router(health_router)
    application.include_router(settings_router)
    application.include_router(telegram_router)
    return application


app = create_app()


def run() -> None:
    uvicorn.run("atlas.api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
