import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "authorization",
    "body",
    "cookie",
    "credential",
    "description",
    "password",
    "prompt",
    "secret",
    "token",
)

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_bearer_pattern = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_credential_url_pattern = re.compile(r"(://[^:/\s]+:)([^@\s]+)(@)")
_telegram_bot_url_pattern = re.compile(r"(?i)(https?://api\.telegram\.org/bot)[^/\s\"']+")
_secret_pattern = re.compile(
    r"(?i)\b(authorization|password|token|secret|api[_-]?key|cookie)"
    r"(\s*[=:]\s*)([^,\s}]+)"
)


def set_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively remove secrets from structured values and common credential strings."""

    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key and any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
        return REDACTED
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, dict):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _bearer_pattern.sub(f"Bearer {REDACTED}", value)
        value = _credential_url_pattern.sub(rf"\1{REDACTED}\3", value)
        value = _telegram_bot_url_pattern.sub(rf"\1{REDACTED}", value)
        return _secret_pattern.sub(rf"\1\2{REDACTED}", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        """Render one redacted log record as compact JSON."""

        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        event_data = getattr(record, "event_data", None)
        if isinstance(event_data, dict):
            payload.update(redact(event_data))
        if record.exc_info and record.exc_info[0]:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)
    # HTTPX request logs include full URLs, which can contain provider credentials.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
