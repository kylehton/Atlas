import httpx
import pytest
from sqlalchemy import create_engine

from atlas.api.app import create_app
from atlas.api.middleware import CORRELATION_HEADER
from atlas.config.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_health_endpoints_and_correlation_header() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    app = create_app(Settings(environment="test"), engine)
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        live_response = await client.get(
            "/health/live", headers={CORRELATION_HEADER: "request-123"}
        )
        ready_response = await client.get("/health/ready")

    assert live_response.status_code == 200
    assert live_response.json() == {"status": "ok"}
    assert live_response.headers[CORRELATION_HEADER] == "request-123"
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ok"}
    assert ready_response.headers[CORRELATION_HEADER]
