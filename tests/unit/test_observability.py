import json
import logging

from atlas.config.observability import (
    JsonFormatter,
    configure_logging,
    reset_correlation_id,
    set_correlation_id,
)


def test_json_logging_redacts_secrets_and_includes_correlation_id() -> None:
    record = logging.LogRecord(
        name="atlas.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "connected to postgresql://user:password@example.com/db with Bearer abc123; "
            "POST https://api.telegram.org/bot123456:telegram-secret/sendMessage"
        ),
        args=(),
        exc_info=None,
    )
    record.event_data = {"event": "test", "token": "abc123", "safe": "visible"}
    context_token = set_correlation_id("request-123")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_correlation_id(context_token)

    assert "password" not in payload["message"]
    assert "abc123" not in payload["message"]
    assert "telegram-secret" not in payload["message"]
    assert "bot[REDACTED]/sendMessage" in payload["message"]
    assert payload["token"] == "[REDACTED]"
    assert payload["safe"] == "visible"
    assert payload["correlation_id"] == "request-123"


def test_provider_http_request_logging_is_suppressed() -> None:
    configure_logging("INFO")

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
