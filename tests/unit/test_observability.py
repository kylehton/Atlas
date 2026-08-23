import json
import logging

from atlas.observability import JsonFormatter, reset_correlation_id, set_correlation_id


def test_json_logging_redacts_secrets_and_includes_correlation_id() -> None:
    record = logging.LogRecord(
        name="atlas.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="connected to postgresql://user:password@example.com/db with Bearer abc123",
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
    assert payload["token"] == "[REDACTED]"
    assert payload["safe"] == "visible"
    assert payload["correlation_id"] == "request-123"
