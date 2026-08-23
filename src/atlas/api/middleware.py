import logging
import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request

from atlas.config.observability import reset_correlation_id, set_correlation_id

logger = logging.getLogger(__name__)
CORRELATION_HEADER = "X-Request-ID"
_valid_correlation_id = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _correlation_id(request: Request) -> str:
    """Reuse a safe request ID from the caller or generate a UUID."""

    supplied = request.headers.get(CORRELATION_HEADER)
    if supplied and _valid_correlation_id.fullmatch(supplied):
        return supplied
    return str(uuid4())


def add_request_context(app: FastAPI) -> None:
    """Attach correlation IDs and structured completion logging to every request."""

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = _correlation_id(request)
        context_token = set_correlation_id(correlation_id)
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[CORRELATION_HEADER] = correlation_id
            logger.info(
                "request completed",
                extra={
                    "event_data": {
                        "event": "http_request_complete",
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    }
                },
            )
            return response
        finally:
            reset_correlation_id(context_token)
