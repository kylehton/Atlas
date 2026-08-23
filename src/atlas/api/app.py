from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy import Engine

from atlas.api.health import router as health_router
from atlas.api.middleware import add_request_context
from atlas.config.config import Settings, get_settings
from atlas.config.observability import configure_logging
from atlas.db.session import create_database_engine


def create_app(settings: Settings | None = None, database_engine: Engine | None = None) -> FastAPI:
    """Create the API with explicit dependencies when provided, or configured defaults."""

    resolved_settings = settings or get_settings()
    resolved_engine = database_engine or create_database_engine(resolved_settings)
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        resolved_engine.dispose()

    application = FastAPI(title="Atlas", version="0.1.0", lifespan=lifespan)
    application.state.settings = resolved_settings
    application.state.database_engine = resolved_engine
    add_request_context(application)
    application.include_router(health_router)
    return application


app = create_app()


def run() -> None:
    uvicorn.run("atlas.api.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
